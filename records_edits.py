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
Every entry also carries "who" and "when". Entries made since 2026-11-10 also carry:
  "id": a short random id (older entries are known by a hash of their content, see ref()),
  "was": the fields the correction changed, with the values the database held just before it (for a removal, what
    was removed: the line's driver, class, place and total, or the event's name, date and number of lines), so the
    Corrections page can show before and after;
  "about": for event and row entries, the event's program, year, date and name when the correction was made.
apply() ignores those three; they are for people and for undo (records.py).
"""
import hashlib
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


def ref(e):
    """How the Corrections page and undo name one entry: its id, or for an older entry a hash of what it says."""
    return e.get('id') or 'h' + hashlib.sha1(json.dumps(e, sort_keys=True, default=str).encode()).hexdigest()[:12]


def clean_name(name):
    return ' '.join((name or '').split())


def norm_class(c):
    """'S TR', 'str' and 'STR' are the same letters: a near-duplicate class."""
    return re.sub(r'\s+', '', (c or '').lower())


def _now_key(e):
    """For a "row" or "standing" entry: the key that finds its row once the entry is applied."""
    k, s = dict(e.get('key') or {}), e.get('set') or {}
    if e.get('kind') == 'row':
        if 'driver' in s:
            k['driver'] = s['driver'].strip()
        return k
    k.pop('nth', None)
    if 'name' in s:
        k['name'] = s['name'].strip()
    if 'class' in s:
        k['class'] = (s['class'] or '').strip()
    if 'place' in s and str(s['place']).strip().isdigit():
        k['place'] = int(s['place'])
    if 'score' in s:
        k['score'] = _num(s['score'])
    return k


def reverse(e):
    """Entries that put back what e changed, on a database that has e applied: [] when that cannot be done in place
    (a removed event or result line is gone from the database; only a rebuild brings it back)."""
    kind, k, s, was = e.get('kind'), e.get('key') or {}, e.get('set') or {}, e.get('was') or {}
    if e.get('remove'):
        if kind == 'standing' and 'place' in k:
            return [{'kind': 'add_standing', 'set': {'program': k.get('program'), 'year': k.get('year'), 'class': k.get('class') or '',
                                                     'place': str(k['place']), 'name': k.get('name'), 'score': k.get('score')}}]
        return []
    if kind == 'event':
        back = {f: was[f] for f in ('name', 'date') if f in s and was.get(f)}
        return [{'kind': 'event', 'key': k, 'set': back}] if back else []
    if kind == 'row':
        back = {f: (k.get('driver') if f == 'driver' else was[f]) for f in s if f in ROW_FIELDS and (f == 'driver' or f in was)}
        return [{'kind': 'row', 'key': _now_key(e), 'set': {f: '' if v is None else str(v) for f, v in back.items()}}] if back else []
    if kind == 'standing':
        back = {}
        for f in s:
            if f in was:
                back[f] = was[f]
            elif f in k:
                back[f] = k[f]
        back = {f: ('' if v is None else ('%g' % v if f == 'score' and isinstance(v, (int, float)) else str(v))) for f, v in back.items()}
        return [{'kind': 'standing', 'key': _now_key(e), 'set': back}] if back else []
    if kind == 'add_standing':
        place = str(s.get('place', '')).strip()
        if not (s.get('name') and place.isdigit()):
            return []
        return [{'kind': 'standing', 'remove': True, 'key': {'program': s.get('program'), 'year': int(s['year']), 'class': (s.get('class') or '').strip(),
                                                             'name': s['name'].strip(), 'place': int(place), 'score': _num(s.get('score')), 'nth': 0}}]
    return []


def present(c, e):
    """For a removal: whether what it removed is in this database (a rebuilt one that never had it applied)."""
    k, kind = e.get('key') or {}, e.get('kind')
    if kind == 'event':
        return bool(c.execute('select 1 from files where drive_url = ? and event_id is not null', (k.get('url'),)).fetchone())
    if kind == 'row':
        return bool(c.execute('select 1 from result_rows r join files f on f.id = r.file_id where f.drive_url = ? and r.line_no = ? and r.driver = ?',
                              (k.get('url'), k.get('line'), k.get('driver'))).fetchone())
    if kind == 'standing':
        n = sum(1 for r in c.execute("select place, score from season_standings where program = ? and year = ? and coalesce(class, '') = ? and name = ?",
                                     (k.get('program'), k.get('year'), k.get('class') or '', k.get('name')))
                if ('place' not in k or str(r[0]) == str(k['place'])) and ('score' not in k or _num(r[1]) == _num(k['score'])))
        return n > (k.get('nth') or 0) if isinstance(k.get('nth', 0), int) else n > 0
    return True


def depends(x, y):
    """True when entry y (made after x) finds its row by what x changed: undoing x alone would leave y with nothing to change."""
    if y.get('kind') != x.get('kind') and not (x.get('kind') == 'add_standing' and y.get('kind') == 'standing'):
        return False
    yk = y.get('key') or {}
    if x.get('kind') == 'row':
        if x.get('remove') or 'driver' not in (x.get('set') or {}):
            return False
        nk = _now_key(x)
        return (yk.get('url'), yk.get('line'), yk.get('driver')) == (nk.get('url'), nk.get('line'), nk.get('driver'))
    if x.get('kind') == 'standing':
        if x.get('remove'):
            return False
        nk = _now_key(x)
    elif x.get('kind') == 'add_standing':
        s = x.get('set') or {}
        nk = {'program': s.get('program'), 'year': s.get('year'), 'class': (s.get('class') or '').strip(), 'name': (s.get('name') or '').strip()}
    else:
        return False
    return (yk.get('program'), str(yk.get('year')), yk.get('class') or '', (yk.get('name') or '').lower()) == \
        (nk.get('program'), str(nk.get('year')), nk.get('class') or '', (nk.get('name') or '').lower())
