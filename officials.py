"""The region's officials (RE, ARE, treasurer, program directors, directors at large) and their emails.

Read-only on purpose: the file is mounted read-only into the container (OFFICIALS_FILE, see docker-compose.prod.yml)
and nothing in the app writes it. Approval emails go only to these addresses, so nobody using the app can make
themselves an approver. Change it on the server.
"""
import json
import os
import threading

PATH = os.environ.get('OFFICIALS_FILE', '/run/secrets/officials.json')

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
