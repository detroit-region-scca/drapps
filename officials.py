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
                    _cache['rows'] = [o for o in json.load(f).get('officials', []) if o.get('name')]
            except (OSError, ValueError):
                _cache['rows'] = []
            _cache['mtime'] = mtime
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


def mirror_to_sheet():
    """Write a readable copy to the Officials tab of the Operations sheet (nightly), so the list is in the shared drive
    too. The app never reads this copy: editing it changes nothing about who approves."""
    from storage import open_book
    sheet = os.environ.get('OPERATIONS_SHEET_ID')
    rows = all()
    if not sheet or not rows:
        return 0
    book = open_book(sheet)
    try:
        ws = book.worksheet('Officials')
    except Exception:
        ws = book.add_worksheet('Officials', rows=100, cols=5)
    out = [['Role', 'Title', 'Name', 'Email', 'Program'],
           *[[o.get('role', ''), o.get('title', ''), o['name'], o.get('email', ''), o.get('program', '')] for o in rows],
           [], ['Copy for reference, refreshed nightly. The app sends approval emails only from the list the admin keeps on the server; '
                'changing this tab does not change who approves.']]
    ws.clear()
    ws.update(out, 'A1', value_input_option='RAW')
    _mirror_permissions(book, rows)
    return len(rows)


def _mirror_permissions(book, rows):
    """The Admin > Roles grid as a readable tab (Role permissions), so it is backed up in the shared drive too."""
    import access
    roles = sorted({o.get('role', '') for o in rows if o.get('role') and o.get('role') != 'Admin'}) + [access.VOLUNTEER]
    grid = [['Permission', *roles]]
    for group, perms in (('Everyday', access.EVERYDAY), ('Extra powers', access.EXTRA), ('Apps on the home page', access.APPS)):
        grid.append([group.upper()])
        have = {r: access.perms(r) for r in roles}
        grid += [[label, *['yes' if k in have[r] else '' for r in roles]] for k, label in perms.items()]
    grid += [[], ['Copy for reference, refreshed nightly. Permissions are set in the apps (Admin > Roles); editing this tab changes nothing.']]
    try:
        ws = book.worksheet('Role permissions')
    except Exception:
        ws = book.add_worksheet('Role permissions', rows=60, cols=len(roles) + 2)
    ws.clear()
    ws.update(grid, 'A1', value_input_option='RAW')
