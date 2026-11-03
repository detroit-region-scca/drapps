"""The region's officials (RE, ARE, treasurer, program directors, directors at large) and their emails.

Read-only on purpose: the file is mounted read-only into the container (OFFICIALS_FILE, see docker-compose.prod.yml)
and nothing in the app writes it. Approval emails go only to these addresses, so nobody using the app can make
themselves an approver. Change it on the server.
"""
import json
import os
import threading

# In the container the file is mounted at /run/secrets; the nightly sync on the host reads it where it lives.
PATH = os.environ.get('OFFICIALS_FILE') or ('/run/secrets/officials.json' if os.path.exists('/run/secrets/officials.json')
                                           else os.environ.get('OFFICIALS_HOST', '/run/secrets/officials.json'))

_lock = threading.Lock()
_cache = {}


def all():  # noqa: A001 (module-level name, read as officials.all())
    """[{role, title, name, email, program?}] in file order; [] when there is no file."""
    try:
        mtime = os.path.getmtime(PATH)
    except OSError:
        return []
    with _lock:
        if _cache.get('mtime') != mtime:
            try:
                with open(PATH) as f:
                    rows = [o for o in json.load(f).get('officials', []) if o.get('name')]
            except (OSError, ValueError):
                # Caught mid-write (the file is rewritten in place): keep the last good list and read again next time.
                return [dict(o) for o in _cache.get('rows', [])]
            _cache['rows'], _cache['mtime'] = rows, mtime
        return [dict(o) for o in _cache['rows']]


def follows(title, titles):
    """The job an entry follows, for entries named after one in brackets ("General Events approver (Region Executive)"
    follows "Region Executive"): whoever holds that job holds this too. '' when it follows none."""
    import re
    m = re.search(r'\(([^()]+)\)\s*$', title or '')
    return m.group(1) if m and m.group(1) in titles else ''


def shown(viewer_roles=()):
    """The list as people see it: the admin account's own entry only for those who may run Admin."""
    import access
    rows = all()
    return rows if 'admin' in access.perms_for(viewer_roles or ()) else [o for o in rows if o.get('role') != 'Admin']


def mailto(o):
    """Where the app emails an official: only the role's group (the "notify" field, e.g. autocross@drscca.org), so mail
    and approval links follow the job. Never the person's own address; '' when the role has no group."""
    return (o.get('notify') or '').strip()


def director(program):
    """(name, address to email) of a program's director, or ('', '')."""
    o = next((o for o in all() if o.get('role') == 'Director' and o.get('program') == program), None)
    return (o['name'], mailto(o)) if o else ('', '')


def director_email(program):
    """The program director's own sign-in address, lowercased, or ''."""
    o = next((o for o in all() if o.get('role') == 'Director' and o.get('program') == program), None)
    return (o.get('email') or '').strip().lower() if o else ''


def signers():
    """[(name, address to email)] who can be the second signer on a check over the limit: the RE and the ARE."""
    return [(o['name'], mailto(o)) for o in all() if o.get('role') in ('RE', 'ARE') and mailto(o)]


def own():
    """Entries a person holds themselves: not the bracketed ones that follow another job's holder."""
    rows = all()
    titles = {o.get('title', '') for o in rows}
    return [o for o in rows if not follows(o.get('title', ''), titles)]


def roles():
    """Every role, in the usual order: the ones people hold and the ones made on Admin > Roles that nobody holds yet
    (not Admin or Volunteer)."""
    import access
    found = {o.get('role', '') for o in own()} | access.saved_roles()
    return sorted(found - {'', 'Admin', access.VOLUNTEER}, key=access.role_key)


def mirror_to_sheet():
    """Write readable copies of the officials list and the Admin > Roles grid to the Operations sheet, so both are in
    the shared drive. Run after every change (and nightly). The app never reads these copies: editing them changes
    nothing. Returns how many officials were written."""
    from storage import open_book
    import access
    sheet = os.environ.get('OPERATIONS_SHEET_ID')
    rows = all()
    if not sheet or not rows:
        return 0
    book = open_book(sheet)
    listed = sorted(rows, key=lambda o: (access.role_key(o.get('role', '')), o.get('title', ''), o.get('name', '')))
    _write_tab(book, 'Officials', ['Role', 'Title', 'Name', 'Email', 'Program', 'Email group'],
               [[o.get('role', ''), o.get('title', ''), o['name'], o.get('email', ''), o.get('program', ''), o.get('notify', '')] for o in listed],
               'Copy for reference, refreshed after every change. Who approves comes only from the list kept on the server '
               '(Admin > Users); editing this tab changes nothing.')
    cols = roles() + [access.VOLUNTEER]
    have = {r: access.perms(r) for r in cols}
    grid = []
    for group, perms in (('Everyday', access.EVERYDAY), ('Extra powers', access.EXTRA), ('Apps on the home page', access.APPS)):
        grid.append([group.upper()])
        grid += [[label, *['yes' if k in have[r] else '' for r in cols]] for k, label in perms.items()]
    _write_tab(book, 'Role permissions', ['Permission', *cols], grid,
               'Copy for reference, refreshed after every change. Permissions are set in the apps (Admin > Roles); '
               'editing this tab changes nothing.')
    return len(rows)


def _write_tab(book, name, header, rows, note):
    """Replace a tab with a header row (bold, frozen), the rows, and a note under them; sized to fit."""
    import gspread
    out = [header, *rows, [], [note]]
    width = max(len(r) for r in out)
    try:
        ws = book.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = book.add_worksheet(name, rows=len(out), cols=width)
    ws.clear()
    ws.resize(rows=len(out), cols=width)
    ws.update(out, 'A1', value_input_option='RAW')
    ws.freeze(rows=1, cols=1)
    ws.format('1:1', {'textFormat': {'bold': True}})


def mirror_soon():
    """Refresh the Drive copies in the background after a change; a failure only waits for the next change or night."""
    import logging
    import threading

    def run():
        try:
            mirror_to_sheet()
        except Exception as e:
            logging.getLogger(__name__).warning(f'officials copy to Drive failed: {e}')
    threading.Thread(target=run, daemon=True).start()
