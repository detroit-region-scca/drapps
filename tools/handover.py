#!/usr/bin/env python3
"""Election-day handover: change who holds a job in officials.json, the one list of who holds which job (the
apps read their approvers from it).

It does not touch the drscca.org Google Groups: group members are changed only in Google (admin.google.com), and
the apps only read the groups (tools/groups_pull.py, for the Email groups page).

    handover.py hand "Autocross Director" new@example.com "New Person" [--from old@example.com] [--apply]

Nothing changes without --apply; without it the tool prints what it would do.
"""
import argparse
import json
import os

OFFICIALS = os.environ.get('OFFICIALS_HOST', os.path.expanduser('~/.config/drevent/officials.json'))
DOMAIN = 'drscca.org'


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
    else:
        print('(dry run: add --apply to save)')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
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
    main()
