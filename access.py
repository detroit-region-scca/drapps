"""What each kind of role may do in the apps: a set of permissions per role (not per person), set by the admin
account in Admin > Roles. Whoever holds a role gets its permissions; someone holding several roles gets them all.

Everyone signed in as a region official can use every app (look, file requests and invoices, add events, enter
worker points). The permissions below are the extra powers. The admin account's own role always has all of them,
so the admin cannot lock themselves out. Volunteers are not a role: they are added one by one and see only the event
schedule and their own requests.

Kept in data/role-access.json as {role: {permission: true/false}}; a role or permission not in the file gets the
defaults below (the bylaws officers have every permission).
"""
import json
import os
import threading

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'role-access.json')

EVERYDAY = {
    'events': 'Add events and build event budgets',
    'funds': 'File funds requests',
    'funds_all': 'See every funds request (otherwise only their own)',
    'invoices': 'Create invoices',
    'invoices_all': 'See every invoice (otherwise only their own)',
    'plan_own': "Write their own program's annual plan",
    'plan_all': "Write any program's annual plan",
    'points_enter': 'Enter worker points for an event',
    'board_ask': 'Send items to the board and propose motions',
}
EXTRA = {
    'board': 'Record board decisions (budget approvals, votes, motions)',
    'pay': 'Mark funds requests and invoices paid',
    'minutes': 'Upload and publish board minutes',
    'points': 'Manage worker points (milestones, auto add, books)',
    'ops': 'Edit Operations (equipment, storage, bills)',
    'access': 'Add and remove volunteers',
    'audit': 'See the audit log',
    'admin': 'Run the Admin app (users, roles, programs)',
}
PERMS = {**EVERYDAY, **EXTRA}
OFFICERS = {'RE', 'ARE', 'Secretary', 'Treasurer', 'Admin'}      # bylaws Art. IV 4.2, plus the admin account
DEFAULTS = {'Equipment': {'ops'}, 'Points Keeper': {'points'}}
EVERYDAY_DEFAULT = set(EVERYDAY) - {'plan_own'}            # what every official could do before roles had settings
VOLUNTEER = 'Volunteer'
VOLUNTEER_DEFAULT = {'funds', 'invoices', 'points_enter'}

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


def _default(role):
    if role == VOLUNTEER:
        return set(VOLUNTEER_DEFAULT)
    return set(PERMS) - {'plan_own'} if role in OFFICERS else EVERYDAY_DEFAULT | set(DEFAULTS.get(role, ()))


def perms(role):
    """The permissions one role has."""
    if role == 'Admin':
        return set(PERMS) - {'plan_own'}
    saved = _load().get(role) or {}
    if 'level' in saved:                        # the earlier full/standard + ops format
        base = (set(PERMS) - {'plan_own', 'admin'}) if saved.get('level') == 'full' else set(EVERYDAY_DEFAULT)
        if saved.get('ops'):
            base.add('ops')
        return base
    out = _default(role)
    for p, on in saved.items():
        if p in PERMS:
            (out.add if on else out.discard)(p)
    return out


def perms_for(roles):
    """Everything a person holding these roles may do."""
    out = set()
    for r in roles or ():
        out |= perms(r)
    return out


def save(role, on):
    """on: the set of permissions this role should have."""
    if role == 'Admin':
        return
    if 'plan_all' in on:
        on = set(on) - {'plan_own'}       # any program includes their own
    doc = _load()
    doc[role] = {p: p in on for p in PERMS}
    tmp = PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    os.replace(tmp, PATH)
    with _lock:
        _cache.clear()
