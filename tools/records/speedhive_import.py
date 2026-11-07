"""Detroit Region SCCA road races from Speedhive (MYLAPS), the timing service the tracks use.

- Waterford Hills (Speedhive organization 30476): the Detroit Region SCCA Regional each year, run alongside the
  track's own race weekend.
- GingerMan Raceway (the organization 39304 the timers post under): the races Detroit Region co-hosted with South Bend
  Region, the Bert Olson National and Majors (2012 on, later "The BERT Sprints"), the Great Lakes Division Spring
  Sprints and the 2023 Fall Sprints. Rounds other regions host alone (Shiloff Series stops) are not Detroit events.

This saves every race session of those weekends (not practice, qualifying, grids or drivers schools) as a CSV file:
position, class position, class, car number, driver, laps, best lap, total time. Nothing else from Speedhive is kept
(no transponder or account ids). A date that already has road racing results from the old website is skipped, so the
same race is never in Records twice.

Each file is uploaded to Drive, *Timing and Results / Results by program / Road Racing / <year> / <track> (Speedhive)*,
and added to the build's work folder (index.json, plan.json, sorted.csv, files/) so build.py reads it.
Run it after fetch.py and classify.py/plan.py, before build.py. Safe to run again: existing files are replaced.

Sessions are named "Group 2 · Saturday race" whatever each upload called them. Unchanged files are not uploaded again,
and this step's own older copies in those folders (renamed or dropped sessions) go to the Drive trash.

Usage: speedhive_import.py <work>      (needs WORKSPACE_SA and TIMING_RESULTS_FOLDER_ID, like the other steps)
"""
import csv
import hashlib
import io
import json
import os
import re
import sys
from collections import defaultdict

import requests

API = 'https://eventresults-api.speedhive.com/api/v0.2.3/eventresults'
WORK = sys.argv[1]


def gingerman_title(name, date):
    n = name.lower()
    if 'spring sprint' in n:
        return 'Spring Sprints at GingerMan'
    if 'fall sprint' in n:
        return 'Fall Sprints at GingerMan'
    return 'Bert Olson National at GingerMan' if date < '2013' else 'Bert Olson Majors at GingerMan'


SOURCES = [
    dict(org=30476, track='Waterford Hills', want=re.compile(r'scca', re.I), years=(0, 9999),
         title=lambda name, date: 'SCCA Regional at Waterford Hills'),
    # Detroit Region co-hosted these with South Bend Region from 2012 (the old website's schedules) through 2024
    dict(org=39304, track='GingerMan', want=re.compile(r'bert|spring sprints|fall sprints', re.I), years=(2012, 2024),
         title=gingerman_title),
]
SKIP = re.compile(r'school', re.I)
NOT_RACE = re.compile(r'grid|practice|warm|time trial|bracket|parade|all cars on track|test', re.I)


def get(path):
    r = requests.get(f'{API}/{path}', headers={'Accept': 'application/json'}, timeout=60)
    r.raise_for_status()
    return r.json()


def races(event):
    """Race sessions, each tagged with its run group number ('_group', from the group's name: "Group 1", "G1-SM, SM5",
    "Grp1"), so sessions get one naming scheme whatever each upload called them."""
    out = []
    for gi, g in enumerate(event.get('sessions', {}).get('groups', []), 1):
        gm = re.search(r'\b(?:group|grp|gp|g)\s*_?#?\s*(\d+)', g.get('name', ''), re.I)
        for s in g.get('sessions', []) + [x for sg in g.get('subGroups', []) for x in sg.get('sessions', [])]:
            if s.get('type') == 'race' and not NOT_RACE.search(s.get('name', '') + ' ' + g.get('name', '')):
                sm = re.search(r'\b(?:group|grp|gp|g)\s*_?#?\s*(\d+)', s.get('name', ''), re.I)
                out.append(dict(s, _group=int((gm or sm)[1]) if (gm or sm) else None))
    return out


def kind(name):
    """What a session was, in one vocabulary: Saturday race, Sunday race, Qualifying race, Points race 1, Feature..."""
    n = name.lower().replace('_', ' ')
    num = re.search(r'race\s*#?\s*(\d)', n)
    if 'qual' in n and 'class race' in n:
        return 'Sunday race (SCCA qualifying)'
    if 'qual' in n:
        return 'Qualifying race' + (' (Sunday)' if 'sun' in n else '')
    if 'feature' in n:
        return 'Feature'
    if 'invitational' in n or 'inviatational' in n:
        return 'Invitational race'
    if 'points' in n and num:
        return f'Points race {num[1]}'
    if re.search(r'\bsat', n):
        return 'Saturday race'
    if re.search(r'\bsun', n):
        return 'Sunday race'
    if num:
        return f'Race {num[1]}'
    return 'Race'


def session_label(s):
    return f"Group {s['_group']} \u00b7 {kind(s['name'])}" if s.get('_group') else kind(s['name'])


def key(s):
    """Group number and race kind, to match a revised session to the one it replaces."""
    return (s.get('_group'), kind(s['name']))


def pick(src, have):
    """One upload per race date (most race sessions, newest), with sessions from any "revised" upload swapped in.
    have: dates that already have road racing results from the old website."""
    events = []
    for off in range(0, 3000, 100):
        page = get(f"organizations/{src['org']}/events?count=100&offset={off}")
        events += page if isinstance(page, list) else page.get('events', [])
        if len(page) < 100:
            break
    by_date = defaultdict(list)
    for e in events:
        date = e['startDate'][:10]
        if (src['want'].search(e['name']) and not SKIP.search(e['name']) and src['years'][0] <= int(date[:4]) <= src['years'][1]
                and date not in have):
            by_date[date].append(get(f"events/{e['id']}?sessions=true"))
    chosen = {}
    for date, evs in by_date.items():
        revised = [e for e in evs if re.search(r'rev', e['name'], re.I)]
        main = max((e for e in evs if e not in revised) or evs, key=lambda e: (len(races(e)), e.get('updatedAt', '')))
        sessions = races(main)
        for e in revised:
            for s in races(e):
                sessions = [x for x in sessions if key(x) != key(s)] + [s]
        chosen[date] = (main, sessions)
    return chosen


def to_csv(src, event, date, session):
    rows = get(f"sessions/{session['id']}/classification").get('rows', [])
    buf = io.StringIO()
    w = csv.writer(buf)
    # first line: the event, the way the build reads event names ("- name - Sat MM-DD-YYYY")
    y, m, d = date.split('-')
    w.writerow([f"Speedhive - {src['title'](event['name'], date)} - Sat {m}-{d}-{y}"])
    w.writerow([f"Session: {session_label(session)}", f"Source: Speedhive event {event['id']}, session {session['id']}"])
    w.writerow(['Pos', 'Class', 'In class', '#', 'Driver', 'Laps', 'Best lap', 'Total time', 'Status'])   # Class before In class: the build takes the first column whose title says class
    for r in rows:
        w.writerow([r.get('position') or '', r.get('resultClass') or '', r.get('positionInClass') or '', r.get('startNumber') or '',
                    ' '.join((r.get('name') or '').split()), r.get('numberOfLaps') or '', r.get('bestTime') or '', r.get('totalTime') or '',
                    '' if (r.get('status') or 'Normal') == 'Normal' else r.get('status')])
    return buf.getvalue(), len(rows)


def drive():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    sa = os.environ.get('WORKSPACE_SA', os.path.expanduser('~/.secrets/drscca-workspace-sa.json'))
    cred = service_account.Credentials.from_service_account_file(sa, scopes=['https://www.googleapis.com/auth/drive']).with_subject('admin@drscca.org')
    return AuthorizedSession(cred)


def main():
    s = drive()
    files_api, up = 'https://www.googleapis.com/drive/v3/files', 'https://www.googleapis.com/upload/drive/v3/files'
    P = {'supportsAllDrives': 'true'}

    def folder(name, parent):
        q = f"'{parent}' in parents and trashed=false and mimeType='application/vnd.google-apps.folder' and name='{name}'"
        f = s.get(files_api, params=dict(P, q=q, includeItemsFromAllDrives='true', fields='files(id)')).json()['files']
        return f[0]['id'] if f else s.post(files_api, params=P, json=dict(name=name, parents=[parent], mimeType='application/vnd.google-apps.folder')).json()['id']

    listed, written = {}, {}

    def listing(parent):
        """{name: (id, md5)} of the CSVs in a folder, read once."""
        if parent not in listed:
            out, tok = {}, None
            while True:
                r = s.get(files_api, params=dict(P, q=f"'{parent}' in parents and trashed=false", includeItemsFromAllDrives='true',
                                                 fields='nextPageToken,files(id,name,md5Checksum)', pageSize=1000, **({'pageToken': tok} if tok else {}))).json()
                out.update({f['name']: (f['id'], f.get('md5Checksum')) for f in r.get('files', [])})
                tok = r.get('nextPageToken')
                if not tok:
                    break
            listed[parent] = out
        return listed[parent]

    root = folder('Road Racing', folder('Results by program', os.environ['TIMING_RESULTS_FOLDER_ID']))
    idx = json.load(open(f'{WORK}/index.json'))
    plan = json.load(open(f'{WORK}/plan.json'))
    sorted_rows = list(csv.DictReader(open(f'{WORK}/sorted.csv')))
    copies = json.load(open(f'{WORK}/copied.json'))
    idx = [f for f in idx if f.get('path') != 'Speedhive']
    plan = [p for p in plan if not p['id'].startswith('speedhive-')]
    sorted_rows = [r for r in sorted_rows if not r['id'].startswith('speedhive-')]
    os.makedirs(f'{WORK}/files', exist_ok=True)
    have = {r['date'] for r in sorted_rows if r['program'] == 'Road Racing' and r.get('date')}
    n = 0
    for src in SOURCES:
      for date, (event, sessions) in sorted(pick(src, have).items()):
        dest = folder(f"{src['track']} (Speedhive)", folder(date[:4], root))
        for sess in sessions:
            body, count = to_csv(src, event, date, sess)
            if not count:
                continue
            name = f"{date} {src['title'](event['name'], date)} - {session_label(sess)}.csv"
            fid = f"speedhive-{sess['id']}"
            open(f'{WORK}/files/{fid}', 'w').write(body)
            meta = {'name': name, 'description': f"Speedhive (MYLAPS) event {event['id']} '{event['name']}', session {sess['id']}"}
            written.setdefault(dest, set()).add(name)
            old, md5 = listing(dest).get(name, (None, None))
            if old and md5 == hashlib.md5(body.encode()).hexdigest():
                copies[fid] = old                  # unchanged: nothing to upload
            elif old:
                s.patch(f'{up}/{old}', params=dict(P, uploadType='media'), data=body.encode(), headers={'Content-Type': 'text/csv'}).raise_for_status()
                copies[fid] = old
            else:
                r = s.post(up, params=dict(P, uploadType='multipart'), files={'metadata': ('m', json.dumps(dict(meta, parents=[dest])), 'application/json'),
                                                                            'file': (name, body.encode(), 'text/csv')})
                r.raise_for_status(); copies[fid] = r.json()['id']
            idx.append({'id': fid, 'name': name, 'mimeType': 'text/csv', 'path': 'Speedhive'})
            plan.append({'id': fid, 'folder': f'Road Racing/{date[:4]}', 'name': name, 'old': 'Speedhive/' + name})
            sorted_rows.append({'id': fid, 'name': name, 'oldpath': 'Speedhive', 'program': 'Road Racing', 'year': date[:4], 'date': date,
                                'kind': 'event', 'text': ''})
            n += 1
    # this step's own older copies (a session renamed, or no longer imported): to the Drive trash
    for dest, names in written.items():
        for name, (fid, _) in listing(dest).items():
            if name.endswith('.csv') and name not in names and name[:10] in {x[:10] for x in names}:   # only the weekends written now
                s.patch(f'{files_api}/{fid}', params=P, json={'trashed': True}).raise_for_status()
                print('trashed old copy', name)
    json.dump(copies, open(f'{WORK}/copied.json', 'w'))
    json.dump(idx, open(f'{WORK}/index.json', 'w'), indent=1)
    json.dump(plan, open(f'{WORK}/plan.json', 'w'), indent=1)
    with open(f'{WORK}/sorted.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(sorted_rows[0]))
        w.writeheader(); w.writerows(sorted_rows)
    print(n, 'race sessions saved from Speedhive')


if __name__ == '__main__':
    main()
