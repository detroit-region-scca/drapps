#!/usr/bin/env python3
"""Read every drscca.org Google Group (address, name, other addresses, members) and save it for the Email groups
page (data/email-groups.json). Read only: members are changed at admin.google.com. The one exception is
hassles@drscca.org, where tools/hassles_subscribe.py applies Subscribe and Unsubscribe from the Hassles page. Run by drscca-groups.path when an admin presses Refresh on the Email groups page.
"""
import csv
import io
import json
import os
import subprocess
from datetime import datetime

GAM = os.environ.get('GAM', os.path.expanduser('~/.local/bin/gam'))
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'email-groups.json')


def gam(*args):
    env = dict(os.environ, GAMCFGDIR=os.environ.get('GAMCFGDIR', os.path.expanduser('~/.gam')))
    r = subprocess.run([GAM, *args], capture_output=True, text=True, env=env)
    if r.returncode:
        raise SystemExit(f'gam {" ".join(args)} failed:\n{r.stderr or r.stdout}')
    return r.stdout


def pull():
    groups = []
    for row in csv.DictReader(io.StringIO(gam('print', 'groups', 'fields', 'email,name,aliases'))):
        g = row['email'].lower()
        out = gam('print', 'group-members', 'group', g, 'fields', 'email,role')
        members = sorted(({'email': m['email'].lower(), 'role': m['role']} for m in csv.DictReader(io.StringIO(out))
                          if m.get('email')), key=lambda m: m['email'])
        groups.append({'email': g, 'name': row.get('name', ''), 'aliases': (row.get('aliases') or '').split(), 'members': members})
    return groups


def main(path=OUT):
    groups = pull()
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({'updated': datetime.now().astimezone().isoformat(timespec='minutes'), 'groups': groups}, f, indent=1)
    os.replace(tmp, path)
    print(f'{len(groups)} groups saved to {path}')


if __name__ == '__main__':
    main()
