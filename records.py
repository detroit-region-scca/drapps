"""Records: the region's results history at /records, for everyone signed in to the apps (volunteers too).

Read-only SQLite at data/records.db, built off the server from every results, standings and points file the region
posted on its old website (1982 to 2026): events, result rows, season standings and the people in them. The heavy work
(reading PDFs, OCR of old scans) happens where the database is built, never here; this module only reads it.
"""
import functools
import os
import re
import sqlite3
from collections import defaultdict
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from storage import DATA_DIR

PATH = os.path.join(DATA_DIR, 'records.db')
PROGRAMS = ['Autocross', 'RallyCross', 'Rally', 'Road Racing']
SLUGS = {re.sub(r'[^a-z]+', '-', p.lower()): p for p in PROGRAMS}
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
def _hall(path, mtime):
    titles = defaultdict(list)
    for s in _q('select person_id, name, program, year, class from season_standings where place = 1 order by year'):
        if real_name(s['name']):
            titles[s['person_id']].append(dict(s))
    names = {r['id']: r['name'] for r in _q('select id, name from people')}
    top = sorted(titles.items(), key=lambda kv: (-len(kv[1]), names.get(kv[0], '')))
    most_titles = [dict(id=pid, name=names.get(pid, ts[0]['name']), n=len(ts), programs=sorted({t['program'] for t in ts}),
                        years=sorted({t['year'] for t in ts})) for pid, ts in top[:25]]
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
    return render_template('records.html', **_ctx(view='home', q=q, people=people, events=events))


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
    return render_template('records.html', **_ctx(view='program', p=p, rows=[(y, ev[y], champs[y]) for y in reversed(ys)], missing=missing))


@bp.route('/<prog>/<int:year>')
def season(prog, year):
    _need()
    p = SLUGS.get(prog) or abort(404)
    events = _q('''select e.id, e.date, e.name, (select count(*) from result_rows r where r.event_id = e.id) n from events e
        where e.program = ? and e.year = ? order by e.date, e.name''', p, year)
    st = _q('select * from season_standings where program = ? and year = ? order by class, place', p, year)
    by_class = defaultdict(list)
    for s in st:
        by_class[s['class']].append(s)
    sheets = _q("select name, drive_url from files where program = ? and year = ? and kind = 'points' order by name", p, year)
    if not events and not st:
        abort(404)
    return render_template('records.html', **_ctx(view='season', p=p, year=year, events=events, by_class=by_class,
                                                  champs=[s for s in st if s['place'] == 1], sheets=sheets))


@bp.route('/event/<int:eid>')
def event(eid):
    _need()
    e = (_q('select * from events where id = ?', eid) or [None])[0] or abort(404)
    files = _q('select id, name, drive_url from files where event_id = ? order by name', eid)
    rows = defaultdict(list)
    for r in _q('select * from result_rows where event_id = ? order by file_id, line_no', eid):
        rows[r['file_id']].append(r)
    cols = {}
    for fid, rs in rows.items():
        cols[fid] = [(lab, k) for lab, k in (('Pos', 'position'), ('Class', 'class'), ('#', 'car_number'), ('Driver', 'driver'),
                     ('Car', 'vehicle'), ('Total', 'total'), ('Raw', 'raw_time'), ('Pax', 'pax_time')) if any(r[k] for r in rs)]
    return render_template('records.html', **_ctx(view='event', e=e, files=files, rows=rows, cols=cols))


@bp.route('/person/<int:pid>')
def person(pid):
    _need()
    p = (_q('select * from people where id = ?', pid) or [None])[0] or abort(404)
    results = _q('''select r.date, r.year, r.program, r.class, r.position, r.vehicle, coalesce(r.total, r.raw_time, r.pax_time) time,
        e.id eid, e.name event from result_rows r join events e on e.id = r.event_id where r.person_id = ? order by r.date, e.name''', pid)
    standings = _q('select * from season_standings where person_id = ? order by year, program', pid)
    years = sorted({r['year'] for r in results if r['year']} | {s['year'] for s in standings})
    return render_template('records.html', **_ctx(view='person', person=p, results=results, standings=standings, years=years,
                                                  n_events=len({r['eid'] for r in results}), n_titles=sum(1 for s in standings if s['place'] == 1),
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
            from storage import UPLOAD, sheets_client
            sess = sheets_client().http_client.session
            for f in files:
                name = ' - '.join(x for x in (when, prog, what, f.filename) if x)[:200]
                meta = {'name': name, 'parents': [INBOX], 'description': f'{prog}, {what}, {when or "no date"}. Added in DRSCCA Apps by {who}.'}
                r = sess.post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id'},
                              files={'metadata': ('metadata', json.dumps(meta), 'application/json'),
                                     'file': (name, f.stream, f.mimetype or 'application/octet-stream')})
                r.raise_for_status()
            log_audit_event('RESULTS_ADDED', who, {'program': prog, 'event': what, 'date': when, 'files': [f.filename for f in files]})
            flash(f"Thanks. {len(files)} file{'s' if len(files) != 1 else ''} saved; the results show in Records after the next update.", 'success')
            return redirect(url_for('records.add'))
    try:
        waiting = _inbox_files()
    except Exception:
        waiting = None
    return render_template('records.html', **_ctx(view='add', waiting=waiting, today=date.today().isoformat()))
