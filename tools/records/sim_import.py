"""DRSCCA Sim Racing (the region's iRacing league) into the records database, as the program "Sim Racing".

Inputs, both kept with the private build data (they hold member names, so never in this repo):
- league.json: the league's seasons (iRacing standings and schedule) and each race's results table, read from the
  league pages at members-ng.iracing.com by a signed-in league member. iRacing's data API needs an OAuth client, and
  iRacing has paused new registrations, so the league pages are the source for now.
  {"seasons": {name: {"standings": [[...]], "schedule": [[date, winner, car, track, subsession, ...]]}},
   "races": {subsession: {"head": [...], "rows": [[...]]}}}
- champions.csv: season,label,class,place,name,source. The league's own podiums: it scores DRSCCA members only and
  splits some seasons into Pro and AM, so iRacing's standings (every driver, one table) are not the championship.
  Each row says where it came from (Discord #champions, the drscca.org season page).

Standings classes are the season and series ("Fall F4 Pro"), since a year has several seasons.
Standings per season: the league's podiums when champions.csv has them; otherwise iRacing's points table when the
league used iRacing's points (a finished season, with points); otherwise none (race results only). A season that is
another one's copy (same races) is skipped.

Each race becomes an event ("Fall 2025 F4: Road America") with one results file, uploaded as a CSV to Drive,
*Timing and Results / Results by program / Sim Racing / <year> / iRacing*. Run after build.py and standings.py, before
dates.py and export_records.py. Safe to run again: earlier Sim Racing rows are replaced.

Usage: sim_import.py <scca.db> <league.json> <champions.csv>   (needs WORKSPACE_SA and TIMING_RESULTS_FOLDER_ID)
"""
import hashlib
import csv
import io
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime

PROGRAM = 'Sim Racing'
SERIES = [(r'mx-?5', 'MX5'), (r'pcup|porsche', 'PCUP'), (r'\bf4\b|formula 4', 'F4'), (r'gt4', 'GT4'),
          (r'ray|ff1600|1600', 'FF1600'), (r'\bsrf\b|spec racer', 'SRF'), (r'gr86', 'GR86'), (r'indy nxt', 'Indy NXT')]
MONTH_SEASON = {1: 'Winter', 2: 'Winter', 3: 'Spring', 4: 'Spring', 5: 'Spring II', 6: 'Spring II', 7: 'Summer', 8: 'Summer',
                9: 'Fall', 10: 'Fall', 11: 'Fall', 12: 'Holiday'}


def series(name):
    return next((s for rx, s in SERIES if re.search(rx, name, re.I)), 'Sim')


def when(text):
    return datetime.strptime(text.strip(), '%b %d, %Y, %I:%M %p')


def label(name, first):
    m = re.search(r'(winter|spring|summer|fall|holiday)\s*(\d{4})?', name, re.I)
    return f"{(m[1].title() if m else MONTH_SEASON[first.month])} {m[2] if m and m[2] else first.year}"


def season_word(lab):
    """'Spring II 2024' -> 'Spring II': standings classes carry the season, since a year has several (Spring MX5, Fall MX5)."""
    return re.sub(r'\s*\d{4}$', '', lab)


def driver(text):
    return re.sub(r'\d+$', '', text).strip()      # iRacing numbers repeated names: "Pat Example2"


def drive():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    sa = os.environ.get('WORKSPACE_SA', os.path.expanduser('~/.secrets/drscca-workspace-sa.json'))
    cred = service_account.Credentials.from_service_account_file(sa, scopes=['https://www.googleapis.com/auth/drive']).with_subject('admin@drscca.org')
    return AuthorizedSession(cred)


class Drive:
    API, UP, P = 'https://www.googleapis.com/drive/v3/files', 'https://www.googleapis.com/upload/drive/v3/files', {'supportsAllDrives': 'true'}

    def __init__(self):
        self.s = drive()
        self._seen = {}

    def _find(self, name, parent, folder=False):
        q = f"'{parent}' in parents and trashed=false and name='{name.replace(chr(39), chr(92) + chr(39))}'"
        if folder:
            q += " and mimeType='application/vnd.google-apps.folder'"
        f = self.s.get(self.API, params=dict(self.P, q=q, includeItemsFromAllDrives='true', fields='files(id)')).json()['files']
        return f[0]['id'] if f else None

    def folder(self, name, parent):
        return self._find(name, parent, True) or self.s.post(self.API, params=self.P, json=dict(
            name=name, parents=[parent], mimeType='application/vnd.google-apps.folder')).json()['id']

    def _listing(self, parent):
        """{name: (id, md5)} of the files in a folder, read once per run (one request per folder, not one per race)."""
        if parent not in self._seen:
            out, token = {}, None
            while True:
                r = self.s.get(self.API, params=dict(self.P, q=f"'{parent}' in parents and trashed=false", includeItemsFromAllDrives='true',
                                                     pageSize=1000, fields='nextPageToken,files(id,name,md5Checksum)', **({'pageToken': token} if token else {})))
                r.raise_for_status()
                body = r.json()
                out.update({f['name']: (f['id'], f.get('md5Checksum')) for f in body.get('files', [])})
                token = body.get('nextPageToken')
                if not token:
                    break
            self._seen[parent] = out
        return self._seen[parent]

    def put(self, name, parent, body, description):
        """Save a race's file in Drive, only when it is new or its contents changed (rebuilds then make no uploads)."""
        old, md5 = self._listing(parent).get(name, (None, None))
        if old and md5 == hashlib.md5(body.encode()).hexdigest():
            return old
        if old:
            self.s.patch(f'{self.UP}/{old}', params=dict(self.P, uploadType='media'), data=body.encode(),
                         headers={'Content-Type': 'text/csv'}).raise_for_status()
            return old
        r = self.s.post(self.UP, params=dict(self.P, uploadType='multipart'), files={
            'metadata': ('m', json.dumps(dict(name=name, parents=[parent], description=description)), 'application/json'),
            'file': (name, body.encode(), 'text/csv')})
        r.raise_for_status()
        return r.json()['id']


def main(db_path, league_path, champs_path):
    league = json.load(open(league_path))
    races = league['races']
    champs = list(csv.DictReader(open(champs_path)))
    db = sqlite3.connect(db_path)
    for t in ('result_rows', 'season_standings', 'files', 'events'):
        db.execute(f'delete from {t} where program = ?', (PROGRAM,))
    people = {n.lower(): i for i, n in db.execute('select id, name from people')}
    next_id = {t: (db.execute(f'select max(id) from {t}').fetchone()[0] or 0) + 1 for t in ('people', 'events', 'files', 'result_rows', 'season_standings')}

    def new(t):
        next_id[t] += 1
        return next_id[t] - 1

    def person(name):
        if not name:
            return None
        if name.lower() not in people:
            people[name.lower()] = pid = new('people')
            db.execute('insert into people (id, name) values (?, ?)', (pid, name))
        return people[name.lower()]

    dr = Drive()
    root = dr.folder(PROGRAM, dr.folder('Results by program', os.environ['TIMING_RESULTS_FOLDER_ID']))
    folders = {}
    seen, n_races, n_rows, n_stand = [], 0, 0, 0
    for name, season in league['seasons'].items():
        sched = [r for r in season['schedule'][1:] if len(r) > 4 and r[4] in races]
        ids = {r[4] for r in sched}
        if not ids or ids in seen:
            continue                                   # no races, or the same races as a season already read
        seen.append(ids)
        dates = sorted(when(r[0]) for r in sched)
        lab, ser = label(name, dates[0]), series(name)
        for r in sched:
            t = when(r[0])
            res = races[r[4]]
            head, rows = res['head'], res['rows']
            col = {h: i for i, h in enumerate(head)}
            ev_name = f"{lab} {ser}: {r[3]}"
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow([f"iRacing - DRSCCA Sim Racing - {ev_name} - {t:%a %m-%d-%Y}"])
            w.writerow([f"Season: {name}", f"Source: iRacing league session {r[4]}"])
            keep = [h for h in head if h not in ('', 'View Laps')]
            w.writerow(['Pos'] + [h for h in keep if h != 'Pos'])
            for pos, row in enumerate(rows, 1):
                w.writerow([pos] + [driver(row[col[h]]) if h == 'Driver' else row[col[h]] for h in keep if h != 'Pos'])
            body = buf.getvalue()
            year = t.year
            if year not in folders:
                folders[year] = dr.folder('iRacing', dr.folder(str(year), root))
            fname = f"{t:%Y-%m-%d} Sim Racing {ev_name.replace(':', ' -')}.csv"
            fid_drive = dr.put(fname, folders[year], body, f"iRacing league DRSCCA Sim Racing, season '{name}', session {r[4]}")
            eid, fid = new('events'), new('files')
            db.execute('insert into events (id, program, year, date, name) values (?, ?, ?, ?, ?)', (eid, PROGRAM, year, f'{t:%Y-%m-%d}', ev_name))
            db.execute('insert into files (id, event_id, program, year, date, kind, name, sorted_name, sorted_folder, original_folder, drive_url, '
                       'rows_found, text) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (fid, eid, PROGRAM, year, f'{t:%Y-%m-%d}', 'event', fname, fname, f'{PROGRAM}/{year}', 'iRacing',
                        f'https://drive.google.com/file/d/{fid_drive}/view', len(rows), body))
            for pos, row in enumerate(rows, 1):
                g = lambda h: row[col[h]].strip() if h in col and col[h] < len(row) else ''  # noqa: E731
                nm = driver(g('Driver'))
                best = re.sub(r'\s*Lap\s*\d+$', '', g('Best Lap'))
                m = re.match(r'(.*?)([A-Za-z ]+)?$', g('Interval'))         # "-0:02.022Running", "---Running", "-1LDisconnected"
                gap = (m[1] or '').strip()
                state = (m[2] or '').strip()
                laps = re.match(r'\d+', g('Laps'))
                car = g('Car')
                db.execute('insert into result_rows (id, file_id, event_id, person_id, program, year, date, section, position, class, car_number, '
                           'driver, vehicle, total, best_lap, laps, status, line_no, raw_line) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           (new('result_rows'), fid, eid, person(nm), PROGRAM, year, f'{t:%Y-%m-%d}', ser, str(pos), ser, g('#'), nm,
                            None if car.lower() == ser.lower() else car or None,       # the car only when it says more than the class
                            None if gap in ('', '---') else gap, best or None, laps[0] if laps else None,
                            None if state.lower() in ('', 'running') else state, pos, ' | '.join(row)))
                n_rows += 1
            n_races += 1
        # standings
        year = dates[0].year
        podium = [c for c in champs if c['season'] == name]
        done = (date.today() - dates[-1].date()).days > 10
        pts = [s for s in season['standings'][1:] if len(s) > 2 and s[2].strip().isdigit()]
        if podium:
            for c in podium:
                db.execute('insert into season_standings (id, program, year, class, place, person_id, name, score, raw_line) values (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           (new('season_standings'), PROGRAM, year, f"{season_word(c['label'])} {c['class']}", int(c['place']), person(c['name']), c['name'], None, c['source']))
                n_stand += 1
        elif done and pts and any(int(s[2]) for s in pts):
            for s in pts:
                db.execute('insert into season_standings (id, program, year, class, place, person_id, name, score, raw_line) values (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           (new('season_standings'), PROGRAM, year, f'{season_word(lab)} {ser}', int(s[0]), person(driver(s[1])), driver(s[1]), float(s[2]),
                            f'iRacing league points, season {name} (all drivers)'))
                n_stand += 1
    # podiums for seasons that are not in the league's iRacing pages (the first season ran before the league existed)
    for c in champs:
        if not c['season']:
            yr = int(re.search(r'\d{4}', c['label'])[0])
            db.execute('insert into season_standings (id, program, year, class, place, person_id, name, score, raw_line) values (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (new('season_standings'), PROGRAM, yr, f"{season_word(c['label'])} {c['class']}", int(c['place']), person(c['name']), c['name'], None, c['source']))
            n_stand += 1
    db.commit()
    print(f'{n_races} sim races, {n_rows} result rows, {n_stand} standings rows')


if __name__ == '__main__':
    main(*sys.argv[1:4])
