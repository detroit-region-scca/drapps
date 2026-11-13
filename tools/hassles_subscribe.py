#!/usr/bin/env python3
"""Apply Subscribe and Unsubscribe from the Hassles page (data/hassles-subscribe.json) to hassles@drscca.org, then
re-read the groups so the Email groups page shows the change. Run by drscca-hassles-subscribe.path when the queue
changes.

This is the one place the apps change a Google Group, and it is kept narrow on purpose (user, 2026-11-13): the group
is fixed below, never taken from the queue; each entry only adds or removes a plain member with that person's own
address (the app queues the signed-in person's address, never one typed in); owners and managers are never touched.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'data')
QUEUE = os.path.join(DATA, 'hassles-subscribe.json')
GROUP = 'hassles@drscca.org'
GAM = os.environ.get('GAM', os.path.expanduser('~/.local/bin/gam'))
EMAIL = re.compile(r'^[^@\s,;"<>]+@[^@\s,;"<>]+\.[a-z]{2,}$', re.I)
sys.path.insert(0, HERE)
from filelock import FileLock  # noqa: E402


def gam(*args):
    env = dict(os.environ, GAMCFGDIR=os.environ.get('GAMCFGDIR', os.path.expanduser('~/.gam')))
    return subprocess.run([GAM, *args], capture_output=True, text=True, env=env)


def roles():
    """{email: role} on the group now."""
    r = gam('print', 'group-members', 'group', GROUP, 'fields', 'email,role')
    if r.returncode:
        raise SystemExit(f'Could not read {GROUP}:\n{r.stderr or r.stdout}')
    import csv
    import io
    return {m['email'].lower(): m['role'] for m in csv.DictReader(io.StringIO(r.stdout)) if m.get('email')}


def apply(changes):
    """The last request per address wins. Returns the lines to log."""
    want = {}
    for c in changes:
        e = (c.get('email') or '').strip().lower()
        if EMAIL.match(e) and c.get('action') in ('add', 'remove'):
            want[e] = c['action']
    have, out = roles(), []
    for e, action in want.items():
        role = have.get(e)
        if action == 'add' and not role:
            r = gam('update', 'group', GROUP, 'add', 'member', 'allmail', 'user', e)
            out.append(f'{GROUP}: added {e}' if not r.returncode else f'{GROUP}: could not add {e}: {(r.stderr or r.stdout).strip()}')
        elif action == 'remove' and role == 'MEMBER':
            r = gam('update', 'group', GROUP, 'delete', 'user', e)
            out.append(f'{GROUP}: removed {e}' if not r.returncode else f'{GROUP}: could not remove {e}: {(r.stderr or r.stdout).strip()}')
        elif action == 'remove' and role:
            out.append(f'{GROUP}: left {e} ({role.lower()}, not a plain member)')
    return out


def main():
    with FileLock(QUEUE + '.lock'):
        try:
            with open(QUEUE) as f:
                changes = json.load(f).get('changes', [])
        except (OSError, ValueError):
            changes = []
        if not changes:
            return
        for line in apply(changes):
            print(line)
        tmp = QUEUE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'changes': []}, f)
        os.replace(tmp, QUEUE)
    import groups_pull
    groups_pull.main()


if __name__ == '__main__':
    main()
