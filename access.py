"""What each kind of role may do in the apps, set per role (not per person) by the admin account.

Levels: 'full' (everything, including recording board decisions, approvals and the audit log) and 'standard'
(every app, but no board decisions). 'ops' adds editing Operations (equipment, storage, bills). The admin
account's own role is always full, so the admin cannot lock themselves out. Volunteers are not a role: they are
added one by one on the Sign-in access page and see only the event schedule and their own requests.

Kept in data/role-access.json; without it, the defaults below (the bylaws officers have full access).
"""
import json
import os
import threading

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'role-access.json')
DEFAULT_FULL = {'RE', 'ARE', 'Secretary', 'Treasurer', 'Admin'}
DEFAULT_OPS = {'Equipment'}
LEVELS = {'full': 'Full access (board decisions, approvals, audit log)', 'standard': 'Standard (every app, no board decisions)'}
_lock = threading.Lock()
_cache = {}


def _load():
    try:
        mtime = os.stat(PATH).st_mtime_ns
    except OSError:
        return {}
    with _lock:
        if _cache.get('mtime') != mtime:
            try:
                with open(PATH) as f:
                    _cache['doc'] = json.load(f)
            except (OSError, ValueError):
                _cache['doc'] = {}
            _cache['mtime'] = mtime
        return dict(_cache['doc'])


def level(role):
    if role == 'Admin':
        return 'full'
    v = (_load().get(role) or {}).get('level')
    return v if v in LEVELS else ('full' if role in DEFAULT_FULL else 'standard')


def edits_ops(role):
    if level(role) == 'full':
        return True
    v = (_load().get(role) or {}).get('ops')
    return bool(v) if v is not None else role in DEFAULT_OPS


def full_roles(roles):
    return {r for r in roles if level(r) == 'full'}


def save(role, lvl, ops):
    if role == 'Admin' or lvl not in LEVELS:
        return
    doc = _load()
    doc[role] = {'level': lvl, 'ops': bool(ops)}
    tmp = PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    os.replace(tmp, PATH)
    with _lock:
        _cache.clear()
