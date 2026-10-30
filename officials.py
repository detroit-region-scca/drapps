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


def director(program):
    """(name, email) of a program's director, or ('', '')."""
    o = next((o for o in all() if o.get('role') == 'Director' and o.get('program') == program), None)
    return (o['name'], o.get('email', '')) if o else ('', '')


def signers():
    """[(name, email)] who can be the second signer on a check over the limit: the RE and the ARE."""
    return [(o['name'], o['email']) for o in all() if o.get('role') in ('RE', 'ARE') and o.get('email')]


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
           [], ['Copy for reference, refreshed nightly. The app sends approval emails only from the list the treasurer keeps on the server; '
                'changing this tab does not change who approves.']]
    ws.clear()
    ws.update(out, 'A1', value_input_option='RAW')
    return len(rows)
