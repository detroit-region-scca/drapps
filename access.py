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
APPS = {
    'show_members': 'Members',
    'show_board': 'Board',
    'show_funds': 'Funds Requests',
    'show_invoices': 'Invoices',
    'show_plan': 'Annual Plan',
    'show_operations': 'Operations',
    'show_treasurer': 'Treasurer',
    'show_volunteers': 'Volunteers',
}
PERMS = {**EVERYDAY, **EXTRA, **APPS}


def _apps_for(p, volunteer=False):
    """The home-page apps that fit these permissions: the default for a role's "Apps on the home page" boxes."""
    show = {
        'show_members': bool(p & {'points_enter', 'points'}),
        'show_board': not volunteer and bool(p & {'board_ask', 'board', 'minutes'}),
        'show_funds': bool(p & {'funds', 'funds_all', 'pay'}),
        'show_invoices': bool(p & {'invoices', 'invoices_all', 'pay'}),
        'show_plan': not volunteer and bool(p & {'plan_own', 'plan_all', 'board', 'pay'}),
        'show_operations': not volunteer and bool(p & {'ops', 'board', 'pay'}),
        'show_treasurer': not volunteer and bool(p & {'pay', 'audit'}),
        'show_volunteers': not volunteer and 'access' in p and 'admin' not in p and not (p & {'pay', 'audit'}),
    }
    return {k for k, v in show.items() if v}
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
    return (set(EVERYDAY) | set(EXTRA)) - {'plan_own'} if role in OFFICERS else EVERYDAY_DEFAULT | set(DEFAULTS.get(role, ()))


def perms(role):
    """The permissions one role has."""
    if role == 'Admin':
        base = set(EVERYDAY) | set(EXTRA)
        return (base - {'plan_own'}) | _apps_for(base - {'plan_own'})
    saved = _load().get(role) or {}
    if 'level' in saved:                        # the earlier full/standard + ops format
        base = ((set(EVERYDAY) | set(EXTRA)) - {'plan_own', 'admin'}) if saved.get('level') == 'full' else set(EVERYDAY_DEFAULT)
        if saved.get('ops'):
            base.add('ops')
        return base | _apps_for(base)
    out = _default(role) - set(APPS)
    for p, on in saved.items():
        if p in PERMS and p not in APPS:
            (out.add if on else out.discard)(p)
    shown = _apps_for(out, role == VOLUNTEER)          # apps follow the permissions unless set by hand
    for p, on in saved.items():
        if p in APPS:
            (shown.add if on else shown.discard)(p)
    return out | shown


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
