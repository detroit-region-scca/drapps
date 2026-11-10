"""Records: the region's results history at /records, for everyone signed in to the apps (volunteers too).

SQLite at data/records.db, built off the server from every results, standings and points file the region posted on
its old website (1982 to 2026): events, result rows, season standings and the people in them. The heavy work (reading
PDFs, OCR of old scans) happens where the database is built, never here. A program's director and the officers can
correct what was read (records_edits.py): each correction goes into data/records-edits.json and is applied to the
live database at once, again whenever a rebuilt database arrives, and by every rebuild. records-base.db is the database
as the last rebuild delivered it (copied before the log is applied); /records/corrections lists the log with before and
after, and Undo rebuilds the live database from that copy without the undone entry (see undo()).
"""
import functools
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date

from filelock import FileLock
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

import records_edits as RE
from storage import DATA_DIR

PATH = os.path.join(DATA_DIR, 'records.db')
PROGRAMS = ['Autocross', 'RallyCross', 'Rally', 'Road Racing', 'Sim Racing']
SLUGS = {re.sub(r'[^a-z]+', '-', p.lower()): p for p in PROGRAMS}
PROGRAM_CODES = {'Autocross': 'AX', 'RallyCross': 'RX', 'Rally': 'RY', 'Road Racing': 'RR'}   # whose director may correct it


def _side(name):
    """Files kept next to records.db: records-edits.json (the corrections log), records-edits.applied (the database
    file the log was last applied to), records-base.db and records-base.json (the database as delivered, for undo)."""
    return os.path.join(os.path.dirname(PATH), name)
bp = Blueprint('records', __name__, url_prefix='/records')
# New results go here first (a Drive folder, RESULTS_INBOX_FOLDER_ID); they are read into the records later, in a batch.
INBOX = os.environ.get('RESULTS_INBOX_FOLDER_ID', '')
KINDS = ('.pdf', '.htm', '.html', '.xls', '.xlsx', '.csv', '.jpg', '.jpeg', '.png')
# Names in the standings that are page text, not people (the sheets are read automatically).
NOT_A_NAME = re.compile(r'\b(series|season|opener|ender|solo|points|event|results?|region|scca|class|total)\b', re.I)


def enabled():
    return os.path.exists(PATH)


def _db():
    c = sqlite3.connect(f'file:{PATH}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def _q(sql, *args):
    c = _db()
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def slug(program):
    return re.sub(r'[^a-z]+', '-', program.lower())


def nice_date(d):
    try:
        return date.fromisoformat(d).strftime('%b %-d, %Y')
    except (TypeError, ValueError):
        return d or ''


def real_name(n):
    return bool(n) and not NOT_A_NAME.search(n) and len(n.split()) >= 2


@functools.lru_cache(maxsize=4)
def _summary(path, mtime):
    """Counts and per-program seasons, cached until the database file changes."""
    n = {k: _q(f'select count(*) from {k}')[0][0] for k in ('events', 'result_rows', 'people')}
    seasons = defaultdict(set)
    for r in _q('select program, year from events where year is not null union select program, year from season_standings'):
        seasons[r['program']].add(r['year'])
    return n, {p: sorted(seasons.get(p, ())) for p in PROGRAMS}


@functools.lru_cache(maxsize=4)
def year_ranges(years):
    """[2006, 2007, 2008, 2010] -> '2006-2008, 2010' (with en dashes), so a long run of seasons stays one short line."""
    out, ys = [], sorted(set(years))
    for y in ys:
        if out and y == out[-1][1] + 1:
            out[-1][1] = y
        else:
            out.append([y, y])
    return ', '.join(f'{a}\u2060\u2013\u2060{b}' if b > a else str(a) for a, b in out)      # word joiners: a range never breaks at its dash


@functools.lru_cache(maxsize=4)
def _hall(path, mtime):
    """The Hall of Fame lists, cached until the database file changes (the Records home page shows the top three)."""
    titles = defaultdict(list)
    for s in _q("select person_id, name, program, year, class from season_standings where place = 1 "
                "and lower(coalesce(class, '')) != 'overall' order by year"):      # an Overall table is not a class
        if real_name(s['name']):
            titles[s['person_id']].append(dict(s))
    names = {r['id']: r['name'] for r in _q('select id, name from people')}
    top = sorted(titles.items(), key=lambda kv: (-len(kv[1]), names.get(kv[0], '')))
    most_titles = [dict(id=pid, name=names.get(pid, ts[0]['name']), n=len(ts), programs=sorted({t['program'] for t in ts}),
                        years=year_ranges(t['year'] for t in ts)) for pid, ts in top[:25]]
    by_prog = {}
    for p in PROGRAMS:
        rows = sorted(((pid, [t for t in ts if t['program'] == p]) for pid, ts in titles.items()), key=lambda kv: (-len(kv[1]), names.get(kv[0], '')))
        by_prog[p] = [dict(id=pid, name=names.get(pid), n=len(ts), classes=sorted({str(t['class']) for t in ts})) for pid, ts in rows if ts][:10]
    events = [dict(r) for r in _q('''select r.person_id id, p.name, count(distinct r.event_id) n, min(r.year) a, max(r.year) b,
        group_concat(distinct r.program) programs from result_rows r join people p on p.id = r.person_id
        where r.year is not null group by r.person_id order by n desc limit 40''') if real_name(r['name'])][:25]
    seasons = [dict(r) for r in _q('''select x.person_id id, p.name, count(distinct x.year) n, min(x.year) a, max(x.year) b from (
        select person_id, year from result_rows where year is not null union select person_id, year from season_standings) x
        join people p on p.id = x.person_id group by x.person_id order by n desc limit 40''') if real_name(r['name'])][:25]
    return dict(titles=most_titles, by_prog=by_prog, events=events, seasons=seasons)


def _ctx(**kw):
    mtime = os.path.getmtime(PATH)
    n, seasons = _summary(PATH, mtime)
    return dict(title='Detroit Region SCCA Records', programs=PROGRAMS, slug=slug, nice_date=nice_date, n=n, seasons=seasons,
                signed_in=bool(session.get('authenticated')), **kw)


def _need():
    if not enabled():
        abort(404)
    _replay()


def _applied():
    try:
        return open(_side('records-edits.applied')).read().strip()
    except FileNotFoundError:
        return ''


def _edits_lock():
    """The app runs several workers: the corrections log is read, added to and written back as one step, and a rebuilt
    database gets the log applied by one worker only."""
    return FileLock(_side('records-edits.json') + '.lock', timeout=60)


def _replay():
    """A rebuilt records.db arrived (its mtime changed): keep a copy of it as it came, then apply the corrections log to
    it once."""
    if _applied() == str(os.path.getmtime(PATH)) and os.path.exists(BASE_PATH()):
        return
    with _edits_lock():
        _sync()


def _copy_db(src, dst):
    """A consistent copy of a SQLite file, swapped in whole."""
    tmp = dst + '.tmp'
    if os.path.exists(tmp):
        os.remove(tmp)
    a, b = sqlite3.connect(src), sqlite3.connect(tmp)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()
    os.replace(tmp, dst)


def _base_meta():
    """records-base.json: which log entries the base copy may already hold. A rebuild applies the log it was given
    (export_records.py), so every entry in the log when a rebuilt database arrives may be in it."""
    import json
    try:
        return json.load(open(_side('records-base.json')))
    except (FileNotFoundError, ValueError):
        return {'in_base': []}


def _save_base(edits):
    import json
    from datetime import datetime
    _copy_db(PATH, BASE_PATH())
    tmp = _side('records-base.json.tmp')
    with open(tmp, 'w') as f:
        json.dump({'in_base': [RE.ref(e) for e in edits], 'made': datetime.now().isoformat(timespec='seconds')}, f)
    os.replace(tmp, _side('records-base.json'))


def BASE_PATH():
    return _side('records-base.db')


def _stamp():
    with open(_side('records-edits.applied'), 'w') as f:
        f.write(str(os.path.getmtime(PATH)))


def _sync():
    """Under the lock: a newly arrived records.db is copied as the base, then gets the log; a records.db that already
    has the log (from before the base copy existed) becomes the base as it is."""
    edits = RE.load(_side('records-edits.json'))
    if _applied() != str(os.path.getmtime(PATH)):
        _save_base(edits)
        if edits:
            c = sqlite3.connect(PATH)
            try:
                RE.apply_all(c, edits)
                c.commit()
            finally:
                c.close()
        _stamp()
    elif not os.path.exists(BASE_PATH()):
        _save_base(edits)


def can_edit(program):
    """The program's director (Admin > Officials) may correct its records, and so may anyone whose role has the
    "records" permission (Admin > Roles; the officers by default)."""
    import auth
    import officials as O
    u = auth.user()
    if not u:
        return False
    if 'records' in (u.get('perms') or ()):
        return True
    code = PROGRAM_CODES.get(program)
    return bool(code) and (u.get('email') or '') == O.director_email(code)


def _record(entries, what, label=''):
    """Save corrections: apply them to the live database, add them to the log, audit them (with before and after)."""
    import uuid
    import auth
    from datetime import datetime
    from utils import log_audit_event
    if not entries:
        return 0
    who = (auth.user() or {}).get('name') or session.get('email') or 'unknown'
    for e in entries:
        e.update(id=uuid.uuid4().hex[:12], who=who, when=datetime.now().isoformat(timespec='seconds'))
    with _edits_lock():
        _sync()                          # the base copy is kept before the first correction ever touches records.db
        c = sqlite3.connect(PATH)
        try:
            for e in entries:
                RE.apply(c, e)
            c.commit()
        finally:
            c.close()
        RE.save(_side('records-edits.json'), RE.load(_side('records-edits.json')) + entries)
        _stamp()
    diff = [x for e in entries for x in short_lines(e)]
    log_audit_event('RECORDS_CORRECTED', who, {'what': what, 'label': label, 'changes': len(entries),
                                               'diff': [x[:120] for x in diff[:AUDIT_MAX]], 'more': max(0, len(diff) - AUDIT_MAX)})
    return len(entries)


AUDIT_MAX = 20       # before/after lines kept in one audit entry; the rest are counted


# ---- what a correction changed, in plain words -------------------------------------------------------------------

LABELS = {'driver': 'Driver', 'name': 'Name', 'class': 'Class', 'position': 'Place', 'place': 'Place', 'total': 'Total', 'score': 'Score',
          'date': 'Date'}
NOT_RECORDED = object()


def ordinal(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n or '')
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _show(f, v):
    if v is None or v == '':
        return '(blank)'
    if f == 'date':
        return nice_date(v)
    if f == 'score' and isinstance(v, float):
        return '%g' % v
    return str(v)


def _changes(e):
    """[(field, before or NOT_RECORDED, after)] for an entry that sets fields."""
    k, s, was = e.get('key') or {}, e.get('set') or {}, e.get('was')
    out = []
    for f, after in s.items():
        if isinstance(was, dict) and f in was:
            before = was[f]
        elif e.get('kind') == 'row' and f == 'driver':
            before = k.get('driver')
        elif e.get('kind') == 'standing' and f in k:
            before = k[f]
        else:
            before = NOT_RECORDED
        out.append((f, before, after))
    return out


def _standing_words(d):
    cl = (d.get('class') or '').strip()
    return f"{d.get('name') or ''}, {ordinal(d.get('place'))}" + (f' in {cl}' if cl else '') if d.get('place') not in (None, '') else \
        (d.get('name') or '') + (f' in {cl}' if cl else '')


def change_lines(e):
    """Lines for the Corrections page: "Driver: Jon Smith -> John Smith", "Removed the event"."""
    kind, k, was = e.get('kind'), e.get('key') or {}, e.get('was') or {}
    if kind == 'add_standing':
        return ['Added ' + _standing_words(e.get('set') or {})]
    if e.get('remove'):
        if kind == 'event':
            return ['Removed the event']
        if kind == 'row':
            return [f"Removed the line for {k.get('driver') or 'a driver'}" + (f", {ordinal(was['position'])}" if str(was.get('position') or '').isdigit() else '')]
        return ['Removed ' + _standing_words(k)]
    out = []
    for f, before, after in _changes(e):
        lab = 'Event name' if kind == 'event' and f == 'name' else LABELS.get(f, f.capitalize())
        if before is NOT_RECORDED:
            out.append(f'{lab}: {_show(f, after)} (before: not recorded)')
        else:
            out.append(f'{lab}: {_show(f, before)} -> {_show(f, after)}')
    return out


def short_lines(e):
    """The same, shorter, for the audit log: "Jon Smith -> John Smith", "class str -> STR"."""
    kind, k = e.get('kind'), e.get('key') or {}
    if kind == 'add_standing':
        return ['added ' + _standing_words(e.get('set') or {})]
    if e.get('remove'):
        return ['removed the event' if kind == 'event' else f"removed the line for {k.get('driver')}" if kind == 'row' else 'removed ' + _standing_words(k)]
    out = []
    for f, before, after in _changes(e):
        b = 'unknown' if before is NOT_RECORDED else _show(f, before)
        if f in ('driver', 'name') and kind != 'event':
            out.append(f'{b} -> {_show(f, after)}')
        else:
            out.append(f"{'event name' if f == 'name' else LABELS.get(f, f).lower()} {b} -> {_show(f, after)}")
    return out


# ---- checks on names and classes before a correction is saved ----------------------------------------------------

def _person_events(c, name):
    """(the person's name as stored, events they have results in) for a name in the people table, else None."""
    r = c.execute('select id, name from people where lower(name) = lower(?)', (name,)).fetchone()
    if not r:
        return None
    n = c.execute('select count(distinct event_id) from result_rows where person_id = ?', (r[0],)).fetchone()[0]
    return r[1], n


def joins_text(name, n):
    return f"This joins {name}'s results ({n} event{'s' if n != 1 else ''})" if n else f"This joins {name}'s results (season standings only)"


def check_name(c, field, value, old, issues, where=''):
    """A changed name: one word is refused; a name that joins another person or makes a new one needs a confirm."""
    v = RE.clean_name(value)
    if v.lower() == RE.clean_name(old).lower():
        return
    if len(v.split()) < 2:
        issues.append(dict(field=field, stop=True, text='Use a first and last name', where=where))
        return
    hit = _person_events(c, v)
    issues.append(dict(field=field, stop=False, text=joins_text(*hit) if hit else 'This creates a new person', where=where,
                       token=f'{field}={value.strip()}'))


def check_classes(changes, gone, counts, season, issues):
    """Changed classes, [(field, old, new, where)], with gone = the classes of lines removed, against counts = {class:
    lines} already in that program and year. A class that differs from one still in use after these changes only by case
    or spaces is refused; one the database does not have yet needs a confirm."""
    after = Counter(counts)
    for _, old, new, _ in changes:
        if (old or '').strip():
            after[old.strip()] -= 1
        if new.strip():
            after[new.strip()] += 1
    for old in gone:
        if (old or '').strip():
            after[old.strip()] -= 1
    for field, old, new, where in changes:
        v = new.strip()
        if not v or v == (old or '').strip():
            continue
        others = after.copy()
        others[v] -= 1
        near = next((c for c in sorted(others) if others[c] > 0 and c != v and RE.norm_class(c) == RE.norm_class(v)), None)
        if near:
            issues.append(dict(field=field, stop=True, text=f'Did you mean {near}?', where=where))
        elif v not in counts:
            issues.append(dict(field=field, stop=False, text=f'{v} is not a class in {season} yet', where=where, token=f'{field}={v}'))


def _held(issues, form):
    """True when the form must be shown again: something refused, or a warning not yet confirmed."""
    ok = set(form.getlist('ok'))
    return any(i['stop'] for i in issues) or any(i['token'] not in ok for i in issues if not i['stop'])


# ---- undo --------------------------------------------------------------------------------------------------------

def undo(ref):
    """Take one entry out of the log and bring the live database back to the state without it.

    records-base.db is records.db as the last rebuild delivered it, before the server applied the log. Undo copies it,
    applies every other entry in order, and swaps the result in. A rebuild applies the log it was given, so an entry
    that was in the log when the rebuilt database arrived may already be in the base copy: such an entry is also put
    back from its "was" values; a removed event or result line it holds cannot be, and comes back with the next
    rebuild (the log no longer has the entry). Returns (entry, restored, why_not) with entry None when it is gone."""
    with _edits_lock():
        _sync()
        log = RE.load(_side('records-edits.json'))
        i = next((n for n, e in enumerate(log) if RE.ref(e) == ref), None)
        if i is None:
            return None, False, 'gone'
        x, rest = log[i], log[:i] + log[i + 1:]
        if any(RE.depends(x, y) for y in log[i + 1:]):
            return x, False, 'later'
        tmp = PATH + '.undo'
        _copy_db(BASE_PATH(), tmp)
        c = sqlite3.connect(tmp)
        try:
            RE.apply_all(c, rest)
            if ref in _base_meta().get('in_base', []):
                for r in RE.reverse(x):
                    RE.apply(c, r)
            restored = RE.present(c, x) if x.get('remove') else True
            c.commit()
        finally:
            c.close()
        os.replace(tmp, PATH)
        RE.save(_side('records-edits.json'), rest)
        _stamp()
    return x, restored, ''


@bp.route('/')
def home():
    _need()
    q = (request.args.get('q') or '').strip()
    people = events = []
    if len(q) >= 2:
        like = f'%{q}%'
        people = _q('''select p.id, p.name, (select count(distinct event_id) from result_rows r where r.person_id = p.id) n,
            (select count(*) from season_standings s where s.person_id = p.id and s.place = 1) t
            from people p where p.name like ? order by n desc limit 30''', like)
        events = _q('select id, program, date, year, name from events where name like ? order by date desc limit 20', like)
    top = _hall(PATH, os.path.getmtime(PATH))['titles'][:3]
    return render_template('records.html', **_ctx(view='home', q=q, people=people, events=events, top=top,
                                                  can_fix=any(can_edit(pg) for pg in PROGRAMS)))


@bp.route('/hall-of-fame')
def hall():
    _need()
    return render_template('records.html', **_ctx(view='hall', h=_hall(PATH, os.path.getmtime(PATH))))


@bp.route('/<prog>')
def program(prog):
    _need()
    p = SLUGS.get(prog) or abort(404)
    ev = defaultdict(int)
    for r in _q('select year, count(*) n from events where program = ? group by year', p):
        ev[r['year']] = r['n']
    champs = defaultdict(int)
    for r in _q('select year, count(*) n from season_standings where program = ? and place = 1 group by year', p):
        champs[r['year']] = r['n']
    ys = _ctx()['seasons'][p]
    missing = [y for y in range(ys[0], ys[-1] + 1) if y not in ys] if ys else []
    top_cars = []
    if any(c['name'] == 'car_make' for c in _q('pragma table_info(result_rows)')):
        top_cars = _q('''select car_make, car_model, count(distinct coalesce(person_id, driver)) drivers, count(distinct event_id) events,
            min(year) a, max(year) b from result_rows where program = ? and car_make is not null and car_model is not null
            group by car_make, car_model order by drivers desc limit 10''', p)
    return render_template('records.html', **_ctx(view='program', p=p, rows=[(y, ev[y], champs[y]) for y in reversed(ys)], missing=missing,
                                                  top_cars=top_cars))


@bp.route('/<prog>/<int:year>')
def season(prog, year):
    _need()
    p = SLUGS.get(prog) or abort(404)
    events = [dict(e) for e in _q('''select e.id, e.date, e.name from events e
        where e.program = ? and e.year = ? order by e.date is null, e.date, e.name''', p, year)]
    _count_entrants(events, p)
    st = _q('select * from season_standings where program = ? and year = ? order by class, place', p, year)
    by_class = defaultdict(list)
    for s in st:
        by_class[s['class']].append(s)
    sheets = _q("select name, drive_url from files where program = ? and year = ? and kind = 'points' order by name", p, year)
    if not events and not st:
        abort(404)
    return render_template('records.html', **_ctx(view='season', p=p, year=year, events=events, by_class=by_class,
                                                  champs=[s for s in st if s['place'] == 1], sheets=sheets, can_edit=can_edit(p)))


def _count_entrants(events, program):
    """Sets e['n'] for each event: drivers (by person, or by name when the name is not linked to a person), or for Rally
    crews, counted on the event's first sheet as the event page does (a crew printed again under a second class counts once)."""
    if not events:
        return
    ids = [e['id'] for e in events]
    marks = ','.join('?' * len(ids))
    rows = _q(f'''select r.event_id, r.file_id, r.line_no, r.person_id, r.driver, coalesce(f.sort, 9) sort, f.name fname from result_rows r
        join files f on f.id = r.file_id where r.event_id in ({marks})''', *ids)
    per = defaultdict(list)
    for r in rows:
        per[r['event_id']].append(r)
    for e in events:
        rs = per.get(e['id'], [])
        if program == 'Rally' and rs:
            first = min((r['sort'], r['fname'], r['file_id']) for r in rs)[2]
            crews = defaultdict(set)
            for r in rs:
                if r['file_id'] == first:
                    crews[r['line_no']].add((r['driver'] or '').lower())
            e['n'] = len({frozenset(c) for c in crews.values()})
        else:
            e['n'] = len({r['person_id'] or (r['driver'] or '').lower() for r in rs})


def _classes(table, program, year):
    """{class: lines} already used in a program and year: the standings' own, or the event results'."""
    return Counter({r[0]: r[1] for r in _q(f"select class, count(*) from {table} where program = ? and year is ? and coalesce(class, '') != '' "
                                           'group by class', program, year)})


@bp.route('/<prog>/<int:year>/edit', methods=['GET', 'POST'])
def season_edit(prog, year):
    """Correct a season's standings: class, place, name and score of each row, remove rows, add missing ones."""
    _need()
    p = SLUGS.get(prog) or abort(404)
    if not can_edit(p):
        abort(403)
    st = _q('select * from season_standings where program = ? and year = ? order by class, place, name', p, year)
    classes, issues = _classes('season_standings', p, year), []
    if request.method == 'POST':
        f, out, cls, gone = request.form, [], [], []
        db = _db()
        same = defaultdict(int)          # rows alike in class, name, place and score: the key says which one (0, 1, ...)
        for s in sorted(st, key=lambda s: s['id']):
            ident = (s['class'] or '', s['name'], s['place'], s['score'])
            k = {'program': p, 'year': year, 'class': s['class'] or '', 'name': s['name'], 'place': s['place'], 'score': s['score'],
                 'nth': same[ident]}
            same[ident] += 1
            if f.get(f'rm{s["id"]}'):
                out.append({'kind': 'standing', 'key': k, 'remove': True})
                gone.append(s['class'])
                continue
            ch = {fld: f.get(f'{fld}{s["id"]}', '').strip() for fld in RE.STANDING_FIELDS if f'{fld}{s["id"]}' in f}
            old = {'class': s['class'] or '', 'place': str(s['place']), 'name': s['name'], 'score': '' if s['score'] is None else '%g' % s['score']}
            ch = {fld: v for fld, v in ch.items() if v != old[fld]}
            if ch:
                where = f"{s['name']}, {ordinal(s['place'])} in {s['class'] or 'no class'}"
                if 'name' in ch:
                    check_name(db, f'name{s["id"]}', ch['name'], s['name'], issues, where)
                if 'class' in ch:
                    cls.append((f'class{s["id"]}', s['class'], ch['class'], where))
                was = {'class': s['class'], 'place': s['place'], 'name': s['name'], 'score': s['score']}
                out.append({'kind': 'standing', 'key': k, 'set': ch, 'was': {fld: was[fld] for fld in ch}})
        out.sort(key=lambda e: -e['key']['nth'])      # of rows that are alike, the later one first, so "which one" still holds for the rest
        for i in range(5):
            nm, pl = f.get(f'new_name{i}', '').strip(), f.get(f'new_place{i}', '').strip()
            cl, sc = f.get(f'new_class{i}', '').strip(), f.get(f'new_score{i}', '').strip()
            if not (nm or pl or cl or sc):
                continue
            where = f'New line {i + 1}'
            if not pl.isdigit():
                issues.append(dict(field=f'new_place{i}', stop=True, text='Give the place as a number', where=where))
            if not nm:
                issues.append(dict(field=f'new_name{i}', stop=True, text='Use a first and last name', where=where))
            else:
                check_name(db, f'new_name{i}', nm, '', issues, where)
            cls.append((f'new_class{i}', None, cl, where))
            out.append({'kind': 'add_standing', 'set': {'program': p, 'year': year, 'class': cl, 'place': pl, 'name': nm, 'score': sc}})
        db.close()
        check_classes(cls, gone, classes, f'{year} {p}', issues)
        if not _held(issues, f):
            n = _record(out, f'standings {p} {year}', f'the {year} {p} standings')
            flash(f'Saved {n} correction{"s" if n != 1 else ""}.' if n else 'Nothing changed.', 'success' if n else 'info')
            return redirect(url_for('records.season', prog=prog, year=year))
    return render_template('records.html', **_ctx(view='season_edit', p=p, year=year, st=st, classes=classes, **_held_ctx(issues)))


def _held_ctx(issues):
    """What the form shows again: the typed values, a message beside each field, and the confirm tokens."""
    if request.method != 'POST':
        return dict(form={}, msgs={}, issues=[], tokens=[], stops=False)
    return dict(form=request.form, msgs={i['field']: i for i in issues}, issues=issues,
                tokens=[i['token'] for i in issues if not i['stop']], stops=any(i['stop'] for i in issues))


@bp.route('/event/<int:eid>')
def event(eid):
    _need()
    e = (_q('select * from events where id = ?', eid) or [None])[0] or abort(404)
    has_ocr = any(r['name'] == 'ocr' for r in _q('pragma table_info(files)'))       # databases built before 2026-11-08 lack it
    files = _q(f"select id, name, drive_url, coalesce(label, 'Results') label, {'ocr' if has_ocr else 'null ocr'} from files "
               'where event_id = ? order by coalesce(sort, 9), name', eid)
    rows = defaultdict(list)
    for r in _q('select * from result_rows where event_id = ? order by file_id, line_no', eid):
        rows[r['file_id']].append(r)
    sheets = []
    car_of = {}                          # Rally: car number -> the car, when the event's sheets agree on one
    if e['program'] == 'Rally':
        seen, names = defaultdict(set), defaultdict(set)
        for rs0 in rows.values():
            for r in rs0:
                if r['car_number'] and r['vehicle']:
                    seen[r['car_number']].add(r['vehicle'])
                    names[r['car_number']].add((r['driver'] or '').lower())
        car_of = {n: (next(iter(v)), names[n]) for n, v in seen.items() if len(v) == 1}
    for f in files:
        rs = rows.get(f['id'])
        if not rs:
            continue
        if e['program'] == 'Rally':      # one line per crew: the rows read from the same line are driver and navigator
            crews = {}
            for r in rs:
                c = crews.setdefault(r['line_no'], dict(r, crew=[]))
                c['crew'].append((r['person_id'], r['driver']))
            rs = list(crews.values())
            for c in rs:                 # a sheet without cars (an overall table) shows the car another sheet of this event printed
                hit = car_of.get(c.get('car_number'))
                if not c.get('vehicle') and hit and {(n or '').lower() for _, n in c['crew']} & hit[1]:   # same number AND a same crew member
                    c['vehicle'] = hit[0]
        real = {(r['driver'], r['car_number']) for r in rs if r['total'] or r['laps']}
        rs = [r for r in rs if not (str(r['position'] or '').upper() == 'DNS' and not (r['total'] or r['laps'])
                                    and (r['driver'], r['car_number']) in real)]     # Speedhive lists a non-start beside the car's own result
        have = set(rs[0].keys())
        cols = [(lab, k) for lab, k in (('Pos', 'position'), ('In class', 'class_place'), ('#', 'car_number'), ('Driver', 'driver'), ('Car', 'vehicle'),
                ('Laps', 'laps'), ('Best lap', 'best_lap'), ('Total', 'total'), ('Raw', 'raw_time'), ('PAX', 'pax_time'), ('Status', 'status'))
                if k in have and any(r[k] for r in rs)
                and not (k == 'class_place' and all(not r[k] or str(r[k]) == str(r['position']) for r in rs))]   # adds nothing beside Pos
        groups = []          # class sheets: one table per class, in the sheet's order; other sheets: one table
        placed = [str(r['position']) for r in rs if str(r['position'] or '').isdigit()]
        is_class = f['label'].startswith('Class results') or (    # places repeat: they are class places, so one table per class
            len(placed) != len(set(placed)) and len({r['class'] for r in rs if r['class']}) > 1)
        if is_class:
            mixed = any(r['section'] and r['class'] and r['section'].lower() != r['class'].lower() for r in rs)
            by = {}
            for r in rs:       # one table per class, in order of first appearance (a sheet in overall order interleaves them)
                key = r['section'] or r['class'] or ''      # the sheet's own headings (a Pro list mixes classes)
                if key not in by:
                    by[key] = []
                    groups.append((key, by[key]))
                by[key].append(r)

            def place(r):
                m = re.match(r'\d+', str(r['position'] or ''))
                return (0, int(m[0])) if m else (1, 0)        # placed rows by place, then DNF/DNS in sheet order
            groups = [(k, sorted(g, key=place)) for k, g in groups]
        else:
            mixed = False
            groups = [('', rs)]
        if not is_class or mixed:
            cols.insert(1, ('Class', 'class'))
        if e['program'] == 'Sim Racing':     # iRacing gives the gap to the winner, not a total time
            cols = [('Gap' if k == 'total' else lab, k) for lab, k in cols]
        if e['program'] == 'Rally':      # a rally crew is a driver and a navigator, scored in points
            cols = [({'driver': 'Crew', 'total': 'Score'}.get(k, lab), k) for lab, k in cols]      # Car only when the sheet printed cars
        keep = ('position', 'driver', 'class')         # per table: leave out a column that is empty in it (a DNS-only table has no Score)
        groups = [(k, g, [(lab, c) for lab, c in cols if (c in keep or any(r[c] for r in g))
                          and not (c == 'class' and len({r['class'] for r in g}) == 1 and (k or len(groups) == 1))])   # one class: no column
                  for k, g in groups]
        same = [x for x in sheets if x['label'] == f['label']]
        lab = f['label'] + (f' {len(same) + 1}' if same else '')
        group, _, part = lab.partition(' \u00b7 ')          # "Group 2 · Saturday race": chooser grouped by run group
        sheets.append(dict(id=f['id'], label=lab, group=group if part else '', part=part or lab, cols=cols, groups=groups))
    everyone = {r['person_id'] or r['driver'] for rs in rows.values() for r in rs}
    if e['program'] == 'Rally':        # crews, counted on the main sheet (a crew is two rows)
        everyone = {frozenset(n for _, n in r['crew']) for _, g, _ in sheets[0]['groups'] for r in g} if sheets else set()
    return render_template('records.html', **_ctx(view='event', e=e, files=files, sheets=sheets, can_edit=can_edit(e['program']),
                                                  scanned=any(f['ocr'] and str(f['ocr']).startswith('read from a scan') and rows.get(f['id']) for f in files),
                                                  n_people=len(everyone), chooser=_chooser(sheets) if len(sheets) > 6 else None))


def _chooser(sheets):
    """[(group, [sheet, ...])] in the sheets' own order: one select list, with an optgroup per run group."""
    out = []
    for sh in sheets:
        if not out or out[-1][0] != sh['group']:
            out.append((sh['group'], []))
        out[-1][1].append(sh)
    return out


@bp.route('/event/<int:eid>/edit', methods=['GET', 'POST'])
def event_edit(eid):
    """Correct an event: its name and date, each result line (name, class, place, score), or remove it."""
    _need()
    e = (_q('select * from events where id = ?', eid) or [None])[0] or abort(404)
    if not can_edit(e['program']):
        abort(403)
    files = _q('select id, name, drive_url, coalesce(label, \'Results\') label from files where event_id = ? order by coalesce(sort, 9), name', eid)
    url = {f['id']: f['drive_url'] for f in files}
    rows = _q('select * from result_rows where event_id = ? order by file_id, line_no, id', eid)
    classes, issues = _classes('result_rows', e['program'], e['year']), []
    about = {'program': e['program'], 'year': e['year'], 'date': e['date'], 'event': e['name']}
    if request.method == 'POST':
        f, out = request.form, []
        key = {'url': files[0]['drive_url']} if files else None
        if key and f.get('remove_event'):
            out.append({'kind': 'event', 'key': key, 'remove': True, 'about': about, 'was': {'name': e['name'], 'date': e['date'], 'lines': len(rows)}})
        elif key:
            ch = {}
            if f.get('name', '').strip() and f['name'].strip() != e['name']:
                ch['name'] = f['name'].strip()
            if f.get('date', '') and f['date'] != (e['date'] or ''):
                ch['date'] = f['date']
            if ch:
                out.append({'kind': 'event', 'key': key, 'set': ch, 'about': about, 'was': {fld: e[fld] for fld in ch}})
            db, cls, gone = _db(), [], []
            season = f"{e['year']} {e['program']}" if e['year'] else e['program']
            for r in rows:
                k = {'url': url[r['file_id']], 'line': r['line_no'], 'driver': r['driver']}
                was = {fld: r[fld] for fld in RE.ROW_FIELDS}
                if f.get(f'rm{r["id"]}'):
                    out.append({'kind': 'row', 'key': k, 'remove': True, 'about': about, 'was': was})
                    gone.append(r['class'])
                    continue
                ch = {fld: f.get(f'{fld}{r["id"]}', '').strip() for fld in RE.ROW_FIELDS if f'{fld}{r["id"]}' in f}
                ch = {fld: v for fld, v in ch.items() if v != (r[fld] or '')}
                if ch:
                    where = (r['driver'] or 'A line') + (f", {r['position']}" if r['position'] else '')
                    if 'driver' in ch:
                        check_name(db, f'driver{r["id"]}', ch['driver'], r['driver'], issues, where)
                    if 'class' in ch:
                        cls.append((f'class{r["id"]}', r['class'], ch['class'], where))
                    out.append({'kind': 'row', 'key': k, 'set': ch, 'about': about, 'was': {fld: was[fld] for fld in ch}})
            db.close()
            check_classes(cls, gone, classes, season, issues)
        if f.get('remove_event') or not _held(issues, f):
            n = _record(out, f"event {e['program']} {e['date'] or e['year']} {e['name']}",
                        f"{e['name']} ({e['program']}, {nice_date(e['date']) or e['year']})")
            flash(f'Saved {n} correction{"s" if n != 1 else ""}.' if n else 'Nothing changed.', 'success' if n else 'info')
            if f.get('remove_event'):
                return redirect(url_for('records.season', prog=slug(e['program']), year=e['year']) if e['year'] else url_for('records.home'))
            return redirect(url_for('records.event', eid=eid))
    labels = {f['id']: f['label'] for f in files}
    return render_template('records.html', **_ctx(view='event_edit', e=e, rows=rows, labels=labels, classes=classes, **_held_ctx(issues)))


NAMES_MAX = 5000
SHOW = 200           # corrections listed before "Show all"


@bp.route('/names.json')
def names_json():
    """[[name, events], ...] for the name pickers: people in the program first (most events first), then everyone else,
    at most NAMES_MAX."""
    from flask import jsonify
    _need()
    p = request.args.get('program') if request.args.get('program') in PROGRAMS else ''
    rows = _q('''select p.name, count(distinct r.event_id) n,
        max(case when r.program = ? then 1 else 0 end) or exists (select 1 from season_standings s where s.person_id = p.id and s.program = ?) mine
        from people p left join result_rows r on r.person_id = p.id group by p.id order by mine desc, n desc, p.name limit ?''', p, p, NAMES_MAX)
    return jsonify([[r['name'], r['n']] for r in rows if len((r['name'] or '').split()) >= 2])


@bp.route('/name.json')
def name_json():
    """What saving a name would do: {"stop": text} for a one-word name, {"joins": name, "n": events, "text"}, or {"new": true, "text"}."""
    from flask import jsonify
    _need()
    v = RE.clean_name(request.args.get('name'))
    if len(v.split()) < 2:
        return jsonify(stop='Use a first and last name')
    c = _db()
    try:
        hit = _person_events(c, v)
    finally:
        c.close()
    return jsonify(joins=hit[0], n=hit[1], text=joins_text(*hit)) if hit else jsonify(new=True, text='This creates a new person')


# ---- the Corrections page ----------------------------------------------------------------------------------------

def _events_by_url(path, urls):
    """{drive link: event} from a records database (the live one, or the base copy for a removed event)."""
    out, urls = {}, list(urls)
    if not urls or not os.path.exists(path):
        return out
    c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    try:
        for i in range(0, len(urls), 500):
            part = urls[i:i + 500]
            for r in c.execute(f'''select f.drive_url, e.program, e.year, e.date, e.name from files f join events e on e.id = f.event_id
                                   where f.drive_url in ({",".join("?" * len(part))})''', part):
                out.setdefault(r['drive_url'], dict(r))
    finally:
        c.close()
    return out


def _views(log):
    """Log entries, newest first, as the Corrections page shows them."""
    urls = {(e.get('key') or {}).get('url') for e in log if e.get('kind') in ('event', 'row')} - {None}
    evs = _events_by_url(PATH, urls)
    evs = {**_events_by_url(BASE_PATH(), urls - set(evs)), **evs}
    in_base = set(_base_meta().get('in_base', []))
    base, may, out = None, {}, []
    try:
        for e in reversed(log):
            kind, k, s = e.get('kind'), e.get('key') or {}, e.get('set') or {}
            ref = RE.ref(e)
            if kind in ('event', 'row'):
                ev = evs.get(k.get('url')) or e.get('about') or {}
                name = ev.get('name') or ev.get('event')
                program = ev.get('program')
                when = nice_date(ev.get('date')) or ev.get('year') or ''
                what = (name or 'An event no longer in Records') + (f', {when}' if when else '')
            else:
                d = k if kind == 'standing' else s
                program, what = d.get('program'), f"{d.get('year')} standings, {(d.get('class') or '').strip() or 'no class'}"
            if program not in may:
                may[program] = can_edit(program)
            back = True                  # a removal that may be in the base copy: is what it removed there?
            if e.get('remove') and kind in ('event', 'row') and ref in in_base:
                if base is None and os.path.exists(BASE_PATH()):
                    base = sqlite3.connect(f'file:{BASE_PATH()}?mode=ro', uri=True)
                back = bool(base) and RE.present(base, e)
            out.append(dict(ref=ref, when=e.get('when'), who=e.get('who') or '', program=program or '', what=what, lines=change_lines(e),
                            can_undo=may[program], back=back))
    finally:
        if base:
            base.close()
    return out


@bp.route('/corrections')
def corrections():
    """Every correction people made, newest first, with what it changed; Undo for those who could have made it."""
    _need()
    log = RE.load(_side('records-edits.json'))
    show_all = bool(request.args.get('all')) or len(log) <= SHOW
    return render_template('records.html', **_ctx(view='corrections', items=_views(log if show_all else log[-SHOW:]), total=len(log), show_all=show_all))


@bp.route('/corrections/undo', methods=['POST'])
def corrections_undo():
    import auth
    from utils import log_audit_event
    _need()
    ref = request.form.get('ref', '')
    x = next((e for e in RE.load(_side('records-edits.json')) if RE.ref(e) == ref), None)
    if x is None:
        flash('That correction was already undone.', 'info')
        return redirect(url_for('records.corrections'))
    view = _views([x])[0]
    if not view['can_undo']:
        abort(403)
    x, restored, why = undo(ref)
    if why == 'gone':
        flash('That correction was already undone.', 'info')
    elif why == 'later':
        flash('A later correction changed the same line. Undo that one first.', 'warning')
    else:
        who = (auth.user() or {}).get('name') or session.get('email') or 'unknown'
        log_audit_event('RECORDS_UNDONE', who, {'program': view['program'], 'label': f"{view['what']} ({view['program']})" if view['program'] else view['what'], 'diff': [y[:120] for y in short_lines(x)][:AUDIT_MAX],
                                                'made_by': x.get('who', ''), 'made_on': (x.get('when') or '')[:10], 'restored': restored})
        flash('Undone.' if restored else 'Undone. What it removed comes back with the next results rebuild.', 'success')
    return redirect(url_for('records.corrections'))


@bp.route('/person/<int:pid>')
def person(pid):
    _need()
    p = (_q('select * from people where id = ?', pid) or [None])[0] or abort(404)
    has_cp = any(c['name'] == 'class_place' for c in _q('pragma table_info(result_rows)'))
    found = _q(f'''select r.date, r.year, r.program, r.class, r.section, {'coalesce(r.class_place, r.position)' if has_cp else 'r.position'} position, r.vehicle, coalesce(r.total, r.raw_time, r.pax_time) time,
        r.pax_time, e.id eid, e.name event, coalesce(f.label, 'Results') label from result_rows r join events e on e.id = r.event_id
        join files f on f.id = r.file_id where r.person_id = ? order by r.date, e.name, f.sort, r.line_no''', pid)
    results, seen = [], {}
    for r in found:            # one line per event: the class result, with the PAX place beside it
        x = seen.get(r['eid'])
        if x is None:
            x = seen[r['eid']] = dict(date=r['date'], year=r['year'], program=r['program'], eid=r['eid'], event=r['event'],
                                      **{'class': None}, position=None, pax=None, vehicle=r['vehicle'], time=None, section=None)
            results.append(x)
        if r['label'] == 'PAX':
            x['pax'] = x['pax'] or r['position']
        elif x['position'] is None or (x['section'] or '').lower() == 'pro':   # a Pro-section line gives way to the driver's own class
            x.update(position=r['position'], time=r['time'], vehicle=r['vehicle'] or x['vehicle'], section=r['section'])
            x['class'] = r['class']
        if not x['time']:
            x['time'] = r['time']
    standings = _q('select * from season_standings where person_id = ? order by year, program', pid)
    years = sorted({r['year'] for r in results if r['year']} | {s['year'] for s in standings})
    cars = []
    if any(c['name'] == 'car_make' for c in _q('pragma table_info(result_rows)')):     # databases built before 2026-11-08 lack it
        cars = _q('''select car_year, car_make, car_model, count(distinct event_id) n, min(year) a, max(year) b from result_rows
            where person_id = ? and car_make is not null group by car_year, car_make, car_model order by max(year) desc, n desc''', pid)
    return render_template('records.html', **_ctx(view='person', person=p, results=results, standings=standings, years=years,
                                                  n_events=len(results), n_titles=sum(1 for s in standings if s['place'] == 1), cars=cars,
                                                  progs=sorted({r['program'] for r in results} | {s['program'] for s in standings})))


def _inbox_files():
    """What is waiting in the inbox, newest first."""
    from storage import DRIVE, sheets_client
    r = sheets_client().http_client.session.get(DRIVE, params={
        'q': f"'{INBOX}' in parents and trashed = false", 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true',
        'orderBy': 'createdTime desc', 'pageSize': 100, 'fields': 'files(name,createdTime,description,webViewLink)'})
    r.raise_for_status()
    return r.json().get('files', [])


@bp.route('/add', methods=['GET', 'POST'])
def add():
    """Drop new results files into the inbox: anyone signed in. Nothing is read automatically."""
    if not INBOX:
        abort(404)
    from utils import log_audit_event
    who = session.get('who') or 'Unknown'
    if request.method == 'POST':
        prog = request.form.get('program') if request.form.get('program') in PROGRAMS else ''
        when = (request.form.get('date') or '').strip()[:10]
        what = re.sub(r'[\\/:*?"<>|]+', ' ', (request.form.get('event') or '')).strip()[:80]
        files = [f for f in request.files.getlist('files') if f and f.filename]
        bad = [f.filename for f in files if not f.filename.lower().endswith(KINDS)]
        if not prog or not what or not files:
            flash('Pick the program, name the event and choose at least one file.', 'warning')
        elif bad:
            flash(f"Results files only (PDF, spreadsheet, web page or picture): {', '.join(bad)}", 'warning')
        else:
            import json
            import logging
            from storage import UPLOAD, sheets_client
            sess = sheets_client().http_client.session
            saved, failed = [], []
            for f in files:
                name = ' - '.join(x for x in (when, prog, what, f.filename) if x)[:200]
                meta = {'name': name, 'parents': [INBOX], 'description': f'{prog}, {what}, {when or "no date"}. Added in DRSCCA Apps by {who}.'}
                try:
                    r = sess.post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id'},
                                  files={'metadata': ('metadata', json.dumps(meta), 'application/json'),
                                         'file': (name, f.stream, f.mimetype or 'application/octet-stream')})
                    r.raise_for_status()
                    saved.append(f.filename)
                except Exception:          # say which files made it: the ones before this one are already in Drive
                    logging.exception(f'Results upload failed for {f.filename}')
                    failed.append(f.filename)
            if saved:
                log_audit_event('RESULTS_ADDED', who, {'program': prog, 'event': what, 'date': when, 'files': saved})
            if failed:
                flash((f"Saved {len(saved)} of {len(files)} files ({', '.join(saved)}). " if saved else 'Google Drive did not answer, so nothing was saved. ')
                      + f"Not saved: {', '.join(failed)}. Add {'just those' if saved else 'them'} again.", 'warning')
            else:
                flash(f"Thanks. {len(files)} file{'s' if len(files) != 1 else ''} saved; the results show in Records after the next update.", 'success')
            return redirect(url_for('records.add'))
    try:
        waiting = _inbox_files()
    except Exception:
        waiting = None
    return render_template('records.html', **_ctx(view='add', waiting=waiting, today=date.today().isoformat()))
