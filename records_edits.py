"""Corrections to the Records database by the people who know the results: a program's director (Rally Director for
Rally, and so on) and the officers.

records.db is rebuilt from the results files (tools/records), so a correction is kept as an entry in a log,
data/records-edits.json, and applied in two places: to the live records.db when it is made (and again whenever a
rebuilt records.db arrives on the server), and by export_records.py at the end of every rebuild. Each entry finds
its target by things a rebuild does not change: the Drive file and line a result came from, or the program, year,
class and name of a standings row.

Entries (dicts):
  {"kind": "event", "key": {"url": <a results file's Drive link>}, "set": {"name": ..., "date": "YYYY-MM-DD"}}
  {"kind": "event", "key": {...}, "remove": true}
  {"kind": "row", "key": {"url": ..., "line": <line_no>, "driver": <name as shown>}, "set": {"driver", "class", "position", "total"}}
  {"kind": "row", "key": {...}, "remove": true}
  {"kind": "standing", "key": {"program", "year", "class", "name", "place", "score", "nth"}, "set": {"class", "place", "name", "score"}}
  {"kind": "standing", "key": {...}, "remove": true}
    A name can be in a class twice (a sheet read twice, two drivers of one name), so the key also carries the row's
    place and score, and "nth" (0, 1, ...) when rows are the same in all of those: one correction changes one row.
    Older entries have only program, year, class and name, and still change every row that matches.
  {"kind": "add_standing", "set": {"program", "year", "class", "place", "name", "score"}}
Every entry also carries "who" and "when".
"""
import json
import os
import re

ROW_FIELDS = ('driver', 'class', 'position', 'total')
STANDING_FIELDS = ('class', 'place', 'name', 'score')


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def save(path, edits):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(edits, f, indent=1)
    os.replace(tmp, path)


def _person(c, name):
    """The person id for a name (case-insensitive), adding the person if new; None for a one-word name."""
    name = ' '.join((name or '').split())
    if len(name.split()) < 2:
        return None
    r = c.execute('select id from people where lower(name) = lower(?)', (name,)).fetchone()
    if r:
        return r[0]
    pid = (c.execute('select max(id) from people').fetchone()[0] or 0) + 1
    c.execute('insert into people (id, name) values (?, ?)', (pid, name))
    return pid


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def apply(c, e):
    """Apply one entry to an open records database. Returns how many rows it changed (0 when its target is gone)."""
    k, kind = e.get('key') or {}, e.get('kind')
    if kind == 'event':
        ids = [r[0] for r in c.execute('select distinct event_id from files where drive_url = ?', (k.get('url'),))]
        n = 0
        for eid in ids:
            if e.get('remove'):
                c.execute('delete from result_rows where event_id = ?', (eid,))
                c.execute('delete from files where event_id = ?', (eid,))
                n += c.execute('delete from events where id = ?', (eid,)).rowcount
                continue
            s = e.get('set') or {}
            if s.get('name'):
                n += c.execute('update events set name = ? where id = ?', (s['name'].strip(), eid)).rowcount
            if s.get('date') and re.fullmatch(r'\d{4}-\d{2}-\d{2}', s['date']):
                n += c.execute('update events set date = ?, year = ? where id = ?', (s['date'], int(s['date'][:4]), eid)).rowcount
                c.execute('update result_rows set date = ?, year = ? where event_id = ?', (s['date'], int(s['date'][:4]), eid))
                c.execute('update files set date = ?, year = ? where event_id = ?', (s['date'], int(s['date'][:4]), eid))
        return n
    if kind == 'row':
        ids = [r[0] for r in c.execute('''select r.id from result_rows r join files f on f.id = r.file_id
            where f.drive_url = ? and r.line_no = ? and r.driver = ?''', (k.get('url'), k.get('line'), k.get('driver')))]
        for rid in ids:
            if e.get('remove'):
                c.execute('delete from result_rows where id = ?', (rid,))
                continue
            s = {f: v for f, v in (e.get('set') or {}).items() if f in ROW_FIELDS}
            if 'driver' in s:
                c.execute('update result_rows set driver = ?, person_id = ? where id = ?', (s['driver'].strip(), _person(c, s['driver']), rid))
            for f in ('class', 'position', 'total'):
                if f in s:
                    c.execute(f'update result_rows set "{f}" = ? where id = ?', ((s[f] or '').strip() or None, rid))
        return len(ids)
    if kind == 'standing':
        ids = [r[0] for r in c.execute('select id, place, score from season_standings where program = ? and year = ? and coalesce(class, \'\') = ? '
                                       'and name = ? order by id', (k.get('program'), k.get('year'), k.get('class') or '', k.get('name')))
               if ('place' not in k or str(r[1]) == str(k['place'])) and ('score' not in k or _num(r[2]) == _num(k['score']))]
        if 'nth' in k:
            ids = ids[k['nth']:k['nth'] + 1] if isinstance(k['nth'], int) and k['nth'] >= 0 else []
        for sid in ids:
            if e.get('remove'):
                c.execute('delete from season_standings where id = ?', (sid,))
                continue
            s = {f: v for f, v in (e.get('set') or {}).items() if f in STANDING_FIELDS}
            if 'name' in s:
                c.execute('update season_standings set name = ?, person_id = ? where id = ?', (s['name'].strip(), _person(c, s['name']), sid))
            if 'class' in s:
                c.execute('update season_standings set class = ? where id = ?', ((s['class'] or '').strip() or None, sid))
            if 'place' in s and str(s['place']).strip().isdigit():
                c.execute('update season_standings set place = ? where id = ?', (int(s['place']), sid))
            if 'score' in s:
                c.execute('update season_standings set score = ? where id = ?', (_num(s['score']), sid))
        return len(ids)
    if kind == 'add_standing':
        s = e.get('set') or {}
        if not (s.get('name') and str(s.get('place', '')).strip().isdigit()):
            return 0
        same = c.execute('select 1 from season_standings where program = ? and year = ? and coalesce(class, \'\') = ? and lower(name) = lower(?)',
                         (s['program'], s['year'], (s.get('class') or '').strip(), s['name'].strip())).fetchone()
        if same:
            return 0                       # already there (the entry is applied again after every rebuild)
        sid = (c.execute('select max(id) from season_standings').fetchone()[0] or 0) + 1
        c.execute('insert into season_standings (id, program, year, class, place, person_id, name, score) values (?, ?, ?, ?, ?, ?, ?, ?)',
                  (sid, s['program'], int(s['year']), (s.get('class') or '').strip() or None, int(s['place']), _person(c, s['name']),
                   s['name'].strip(), _num(s.get('score'))))
        return 1
    return 0


def apply_all(c, edits):
    """Apply every entry in order. Returns how many found their target."""
    return sum(1 for e in edits if apply(c, e))
