"""Records: the region's results history at /records, for everyone signed in to the apps (volunteers too).

SQLite at data/records.db, built off the server from every results, standings and points file the region posted on
its old website (1982 to 2026): events, result rows, season standings and the people in them. The heavy work (reading
PDFs, OCR of old scans) happens where the database is built, never here. A program's director and the officers can
correct what was read (records_edits.py): each correction goes into data/records-edits.json and is applied to the
live database at once, again whenever a rebuilt database arrives, and by every rebuild.
"""
import functools
import os
import re
import sqlite3
from collections import defaultdict
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

import records_edits as RE
from storage import DATA_DIR

PATH = os.path.join(DATA_DIR, 'records.db')
PROGRAMS = ['Autocross', 'RallyCross', 'Rally', 'Road Racing', 'Sim Racing']
SLUGS = {re.sub(r'[^a-z]+', '-', p.lower()): p for p in PROGRAMS}
PROGRAM_CODES = {'Autocross': 'AX', 'RallyCross': 'RX', 'Rally': 'RY', 'Road Racing': 'RR'}   # whose director may correct it


def _side(name):
    """Files kept next to records.db: records-edits.json (the corrections log) and records-edits.applied (the database
    file the log was last applied to)."""
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
    _replay()


def _replay():
    """A rebuilt records.db arrived (its mtime changed): apply the corrections log to it once."""
    try:
        mark = open(_side('records-edits.applied')).read().strip()
    except FileNotFoundError:
        mark = ''
    now = str(os.path.getmtime(PATH))
    if mark == now:
        return
    edits = RE.load(_side('records-edits.json'))
    if edits:
        c = sqlite3.connect(PATH)
        try:
            RE.apply_all(c, edits)
            c.commit()
        finally:
            c.close()
    with open(_side('records-edits.applied'), 'w') as f:
        f.write(str(os.path.getmtime(PATH)))


def can_edit(program):
    """The program's director (Admin > Officials) and the officers may correct a program's records."""
    import auth
    import officials as O
    u = auth.user()
    if not u:
        return False
    if set(u.get('roles') or ()) & set(auth.OFFICER_ROLES):
        return True
    code = PROGRAM_CODES.get(program)
    return bool(code) and (u.get('email') or '') == O.director_email(code)


def _record(entries, what):
    """Save corrections: apply them to the live database, add them to the log, audit them."""
    import auth
    from datetime import datetime
    from utils import log_audit_event
    if not entries:
        return 0
    who = (auth.user() or {}).get('name') or session.get('email') or 'unknown'
    for e in entries:
        e.update(who=who, when=datetime.now().isoformat(timespec='seconds'))
    c = sqlite3.connect(PATH)
    try:
        for e in entries:
            RE.apply(c, e)
        c.commit()
    finally:
        c.close()
    RE.save(_side('records-edits.json'), RE.load(_side('records-edits.json')) + entries)
    with open(_side('records-edits.applied'), 'w') as f:
        f.write(str(os.path.getmtime(PATH)))
    log_audit_event('RECORDS_CORRECTED', who, {'what': what, 'changes': len(entries)})
    return len(entries)


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
    events = _q('''select e.id, e.date, e.name, (select count(distinct r.person_id) from result_rows r where r.event_id = e.id) n from events e
        where e.program = ? and e.year = ? order by e.date is null, e.date, e.name''', p, year)
    st = _q('select * from season_standings where program = ? and year = ? order by class, place', p, year)
    by_class = defaultdict(list)
    for s in st:
        by_class[s['class']].append(s)
    sheets = _q("select name, drive_url from files where program = ? and year = ? and kind = 'points' order by name", p, year)
    if not events and not st:
        abort(404)
    return render_template('records.html', **_ctx(view='season', p=p, year=year, events=events, by_class=by_class,
                                                  champs=[s for s in st if s['place'] == 1], sheets=sheets, can_edit=can_edit(p)))


@bp.route('/<prog>/<int:year>/edit', methods=['GET', 'POST'])
def season_edit(prog, year):
    """Correct a season's standings: class, place, name and score of each row, remove rows, add missing ones."""
    _need()
    p = SLUGS.get(prog) or abort(404)
    if not can_edit(p):
        abort(403)
    st = _q('select * from season_standings where program = ? and year = ? order by class, place, name', p, year)
    if request.method == 'POST':
        f, out = request.form, []
        for s in st:
            k = {'program': p, 'year': year, 'class': s['class'] or '', 'name': s['name']}
            if f.get(f'rm{s["id"]}'):
                out.append({'kind': 'standing', 'key': k, 'remove': True})
                continue
            ch = {fld: f.get(f'{fld}{s["id"]}', '').strip() for fld in RE.STANDING_FIELDS if f'{fld}{s["id"]}' in f}
            old = {'class': s['class'] or '', 'place': str(s['place']), 'name': s['name'], 'score': '' if s['score'] is None else '%g' % s['score']}
            ch = {fld: v for fld, v in ch.items() if v != old[fld]}
            if ch:
                out.append({'kind': 'standing', 'key': k, 'set': ch})
        for i in range(5):
            nm, pl = f.get(f'new_name{i}', '').strip(), f.get(f'new_place{i}', '').strip()
            if nm and pl.isdigit():
                out.append({'kind': 'add_standing', 'set': {'program': p, 'year': year, 'class': f.get(f'new_class{i}', '').strip(),
                                                            'place': pl, 'name': nm, 'score': f.get(f'new_score{i}', '').strip()}})
        n = _record(out, f'standings {p} {year}')
        flash(f'Saved {n} correction{"s" if n != 1 else ""}.' if n else 'Nothing changed.', 'success' if n else 'info')
        return redirect(url_for('records.season', prog=prog, year=year))
    return render_template('records.html', **_ctx(view='season_edit', p=p, year=year, st=st))


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
        have = set(rs[0].keys())
        cols = [(lab, k) for lab, k in (('Pos', 'position'), ('In class', 'class_place'), ('#', 'car_number'), ('Driver', 'driver'), ('Car', 'vehicle'),
                ('Laps', 'laps'), ('Best lap', 'best_lap'), ('Total', 'total'), ('Raw', 'raw_time'), ('PAX', 'pax_time'), ('Status', 'status'))
                if k in have and any(r[k] for r in rs)
                and not (k == 'class_place' and all(not r[k] or str(r[k]) == str(r['position']) for r in rs))]   # adds nothing beside Pos
        groups = []          # class sheets: one table per class, in the sheet's order; other sheets: one table
        is_class = f['label'].startswith('Class results')
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
            groups = [('', rs)]
        if not is_class or mixed:
            cols.insert(1, ('Class', 'class'))
        if e['program'] == 'Rally':      # a rally crew is a driver and a navigator, scored in points
            cols = [({'driver': 'Crew', 'total': 'Score'}.get(k, lab), k) for lab, k in cols if k != 'vehicle']
        same = [x for x in sheets if x['label'] == f['label']]
        lab = f['label'] + (f' {len(same) + 1}' if same else '')
        group, _, part = lab.partition(' \u00b7 ')          # "Group 2 · Saturday race": chooser grouped by run group
        sheets.append(dict(id=f['id'], label=lab, group=group if part else '', part=part or lab, cols=cols, groups=groups))
    everyone = {r['person_id'] or r['driver'] for rs in rows.values() for r in rs}
    if e['program'] == 'Rally':        # crews, counted on the main sheet (a crew is two rows)
        everyone = {r['line_no'] for r in rows.get(sheets[0]['id'], [])} if sheets else set()
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
    if request.method == 'POST':
        f, out = request.form, []
        key = {'url': files[0]['drive_url']} if files else None
        if key and f.get('remove_event'):
            out.append({'kind': 'event', 'key': key, 'remove': True})
        elif key:
            ch = {}
            if f.get('name', '').strip() and f['name'].strip() != e['name']:
                ch['name'] = f['name'].strip()
            if f.get('date', '') and f['date'] != (e['date'] or ''):
                ch['date'] = f['date']
            if ch:
                out.append({'kind': 'event', 'key': key, 'set': ch})
            for r in rows:
                k = {'url': url[r['file_id']], 'line': r['line_no'], 'driver': r['driver']}
                if f.get(f'rm{r["id"]}'):
                    out.append({'kind': 'row', 'key': k, 'remove': True})
                    continue
                ch = {fld: f.get(f'{fld}{r["id"]}', '').strip() for fld in RE.ROW_FIELDS if f'{fld}{r["id"]}' in f}
                ch = {fld: v for fld, v in ch.items() if v != (r[fld] or '')}
                if ch:
                    out.append({'kind': 'row', 'key': k, 'set': ch})
        n = _record(out, f"event {e['program']} {e['date'] or e['year']} {e['name']}")
        flash(f'Saved {n} correction{"s" if n != 1 else ""}.' if n else 'Nothing changed.', 'success' if n else 'info')
        if f.get('remove_event'):
            return redirect(url_for('records.season', prog=slug(e['program']), year=e['year']) if e['year'] else url_for('records.home'))
        return redirect(url_for('records.event', eid=eid))
    labels = {f['id']: f['label'] for f in files}
    return render_template('records.html', **_ctx(view='event_edit', e=e, rows=rows, labels=labels))


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
    return render_template('records.html', **_ctx(view='person', person=p, results=results, standings=standings, years=years,
                                                  n_events=len(results), n_titles=sum(1 for s in standings if s['place'] == 1),
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
