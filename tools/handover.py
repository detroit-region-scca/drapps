#!/usr/bin/env python3
"""Election-day handover: change who holds a job, in one place, and bring the drscca.org groups in line.

officials.json (the file drevent reads its approvers from) is the one list of who holds which job. This tool
edits it and then makes the Google Groups match it:

    re@, are@, secretary@, autocross@, roadracing@, rally@, rallycross@, sim@   the holder of that job
    equipment@                                                     the Equipment Committee entries
    bod@                                                           every board seat
    officers@                                                      RE, ARE, Secretary, Treasurer (bylaws IV 4.2)

Only plain MEMBERs are added or removed; owners and managers (treasurer@) are never touched.
Nothing changes without --apply; without it the tool prints what it would do.

    handover.py show
    handover.py sync [--apply]
    handover.py hand "Autocross Director" new@example.com "New Person" [--from old@example.com] [--apply]

Runs on bodhi (GAM as the region's service account, GAMCFGDIR=~/.gam).
"""
import argparse
import csv
import io
import json
import os
import subprocess
import sys

OFFICIALS = os.environ.get('OFFICIALS_HOST', os.path.expanduser('~/.config/drevent/officials.json'))
GAM = os.environ.get('GAM', os.path.expanduser('~/.local/bin/gam'))
DOMAIN = 'drscca.org'

ROLE_GROUP = {           # job title -> its group
    'Region Executive': 're',
    'Secretary': 'secretary',
    'Assistant Region Executive': 'are',
    'Road Racing Director': 'roadracing',
    'Autocross Director': 'autocross',
    'Road Rally Director': 'rally',
    'Rallycross Director': 'rallycross',
    'SIM Racing': 'sim',
    'Street Survival Director': 'streetsurvival',
}
BOARD_ROLES = {'RE', 'ARE', 'Secretary', 'Treasurer', 'Financial Director', 'Director', 'Director at Large'}
OFFICER_ROLES = {'RE', 'ARE', 'Secretary', 'Treasurer'}   # bylaws Art. IV 4.2


def load():
    with open(OFFICIALS) as f:
        return json.load(f)


def save(doc):
    # In place (same inode): the container mounts this single file, and a replaced file never reaches it.
    text = json.dumps(doc, indent=2, ensure_ascii=False) + '\n'
    with open(OFFICIALS, 'r+') as f:
        f.seek(0)
        f.write(text)
        f.truncate()


def wanted(officials):
    """{group: set(emails)} as officials.json says it should be."""
    out = {g: set() for g in list(ROLE_GROUP.values()) + ['equipment', 'bod', 'officers']}
    for o in officials:
        e = (o.get('email') or '').strip().lower()
        if not e or not o.get('name'):
            continue
        if o.get('title') in ROLE_GROUP:
            out[ROLE_GROUP[o['title']]].add(e)
        if o.get('role') == 'Equipment':
            out['equipment'].add(e)
        if o.get('role') in BOARD_ROLES:
            out['bod'].add(e)
        if o.get('role') in OFFICER_ROLES:
            out['officers'].add(e)
    return out


def gam(*args):
    env = dict(os.environ, GAMCFGDIR=os.environ.get('GAMCFGDIR', os.path.expanduser('~/.gam')))
    r = subprocess.run([GAM, *args], capture_output=True, text=True, env=env)
    if r.returncode:
        raise SystemExit(f'gam {" ".join(args)} failed:\n{r.stderr or r.stdout}')
    return r.stdout


def members(group):
    """{email: role} of the group's direct members."""
    out = gam('print', 'group-members', 'group', f'{group}@{DOMAIN}', 'fields', 'email,role')
    return {row['email'].lower(): row['role'] for row in csv.DictReader(io.StringIO(out)) if row.get('email')}


def plan(want):
    """[(group, 'add'|'remove', email)] that would make the groups match."""
    steps = []
    for group, emails in want.items():
        have = members(group)
        steps += [(group, 'add', e) for e in sorted(emails - set(have))]
        steps += [(group, 'remove', e) for e, role in sorted(have.items()) if role == 'MEMBER' and e not in emails]
    return steps


def run(steps, apply):
    if not steps:
        print('Groups already match officials.json.')
        return
    for group, op, email in steps:
        print(f'  {group}@{DOMAIN}: {op} {email}')
        if apply:
            gam('update', 'group', f'{group}@{DOMAIN}', op, 'member', email)
    print('Done.' if apply else '(dry run: add --apply to make these changes)')


def cmd_show(_):
    for group, emails in wanted(load()['officials']).items():
        print(f'{group}@{DOMAIN}: {", ".join(sorted(emails)) or "(nobody)"}')


def cmd_sync(a):
    run(plan(wanted(load()['officials'])), a.apply)


def cmd_hand(a):
    doc = load()
    rows = doc['officials']
    title = a.title.strip()
    hits = [o for o in rows if o.get('title', '').lower() == title.lower()]
    if not hits:
        raise SystemExit(f'No job titled "{title}". Titles: ' + ', '.join(sorted({o.get("title", "") for o in rows})))
    if a.old:
        hits = [o for o in hits if o.get('email', '').lower() == a.old.lower()]
        if not hits:
            raise SystemExit(f'Nobody with {a.old} holds "{title}".')
    elif len({o.get('email', '').lower() for o in hits}) > 1:
        raise SystemExit(f'Several people hold "{title}"; say which one with --from <their email>.')
    old_email, old_name = hits[0].get('email', ''), hits[0].get('name', '')
    if old_email.lower().endswith('@' + DOMAIN):
        raise SystemExit(f'{old_email} is a region account: hand over its password instead, then fix the name here by hand.')
    # The job itself, plus entries that name it in brackets ("General Events approver (Region Executive)").
    changed = [o for o in rows if o.get('email', '').lower() == old_email.lower()
               and (o.get('title', '').lower() == title.lower() or f'({title.lower()})' in o.get('title', '').lower())]
    print(f'{title}: {old_name} <{old_email}>  ->  {a.name} <{a.email}>')
    for o in changed:
        print(f'  officials.json: {o["title"]}')
        o['name'], o['email'] = a.name, a.email
    keeps = [o['title'] for o in rows if o.get('email', '').lower() == old_email.lower() and o not in changed]
    if keeps:
        print(f'  {old_name} keeps: {", ".join(keeps)}')
    if a.apply:
        save(doc)
    run(plan(wanted(rows)), a.apply)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('show', help='who each group should reach, per officials.json').set_defaults(fn=cmd_show)
    s = sub.add_parser('sync', help='make the groups match officials.json')
    s.add_argument('--apply', action='store_true')
    s.set_defaults(fn=cmd_sync)
    h = sub.add_parser('hand', help='give a job to someone new')
    h.add_argument('title', help='job title as in officials.json, e.g. "Autocross Director"')
    h.add_argument('email')
    h.add_argument('name')
    h.add_argument('--from', dest='old', help='current holder, when several people share the title')
    h.add_argument('--apply', action='store_true')
    h.set_defaults(fn=cmd_hand)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
