"""Build scca.db: every DRSCCA results file, its text, and every table row found in it.

Inputs (in $WORK): plan.json, sorted.csv, copied.json from the Drive sort, files/<drive id>, and text/<id>.txt
(pdftotext -layout). Output: data/scca.db (SQLite, opened read-only by Datasette).
"""
import csv, json, os, re, sqlite3, sys
WORK, OUT = sys.argv[1], sys.argv[2]
TEXT = os.path.join(os.path.dirname(OUT), 'text')   # pdftotext / OCR output, next to the db
plan = {p['id']: p for p in json.load(open(f'{WORK}/plan.json'))}
meta = {r['id']: r for r in csv.DictReader(open(f'{WORK}/sorted.csv'))}
copies = json.load(open(f'{WORK}/copied.json'))
fallback = json.load(open(f'{WORK}/text.json'))

LABELS = [  # (field, header patterns) first match wins
    ('position', r'^(pos\.?|fin|place|oa|overall|p)$'), ('class', r'^(class|cls|cla)$'),
    ('car_number', r'^(#|car ?#|no\.?|number|car no\.?)$'), ('driver', r'^(driver|name|competitor.*|driver/navigator|team)$'),
    ('first', r'^first ?name$'), ('last', r'^last ?name$'), ('vehicle', r'^(car model|vehicle|car|yr make model.*|make model|model)$'),
    ('color', r'^(car )?color$'), ('pax_time', r'^pax ?time$'), ('raw_time', r'^raw ?time$'), ('total', r'^(total|total ?time|best|score)$'),
]
HEAD_WORDS = re.compile(r'\b(driver|class|pos\.?|name|total|vehicle|car)\b', re.I)
TIME = re.compile(r'^-?\d{1,4}\.\d{1,4}')
def cells(line):
    return [(m.start(), m.end(), m.group().strip()) for m in re.finditer(r'\S+(?: \S+)*', line)]
def field(label):
    l = ' '.join(label.lower().split())
    if re.match(r'^first ?name', l): return 'first'
    if re.match(r'^last ?name', l): return 'last'
    if re.match(r'^member ?#?', l): return 'member_no'
    if l in ('ann', 'exp', 'expires'): return 'anniversary'
    if 'pax' in l and 'time' in l: return 'pax_time'
    if 'raw' in l and 'time' in l: return 'raw_time'
    if re.search(r'color', l): return 'color'
    if re.search(r'driver|competitor|navigator|\bname\b|team', l): return 'driver'
    if re.search(r'car model|vehicle|make|\bmodel\b|^car$', l): return 'vehicle'
    if re.search(r'\bclass\b|^cls$|^cla$', l): return 'class'
    if re.search(r'\bpos\.?\b|^fin$|place|^oa$|overall', l): return 'position'
    if re.match(r'^(#|car ?#|no\.?|number|car no\.?)$', l): return 'car_number'
    if re.match(r'^(total|total ?time|best|score)$', l): return 'total'
    return None
def parse(text):
    rows, header, section = [], None, ''
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip(): continue
        cs = cells(line)
        heads = sum(1 for c in cs if field(c[2]))
        if heads >= 3 and HEAD_WORDS.search(line) and not any(TIME.match(c[2]) for c in cs):
            header = [(a, b, c, field(c)) for a, b, c in cs]; continue
        if header is None: continue
        if len(cs) == 1 and not TIME.match(cs[0][2]) and len(cs[0][2]) < 60:
            section = cs[0][2].strip("' "); continue
        if len(cs) < 3 or re.match(r'(page \d|timing by|final results|results|copyright|©)', line.strip(), re.I): continue
        rec = {'cells': []}
        for a, b, c in cs:
            mid = (a + b) / 2
            h = min(header, key=lambda h: min(abs(mid - (h[0] + h[1]) / 2), abs(a - h[0]), abs(b - h[1])))
            f = h[3]
            # merged "class # driver" or "# driver" cells
            m = re.match(r'^([a-z][a-z0-9]{0,7})\s+(\d{1,4}[a-z]?)\s+([A-Za-z].+)$', c)
            if f in ('class', 'car_number', 'driver') and m:
                rec.setdefault('class', m[1]); rec.setdefault('car_number', m[2]); rec.setdefault('driver', m[3]); continue
            m = re.match(r'^(\d{1,4}[a-z]?)\s+([A-Z][A-Za-z\'\-.]+(?: [A-Za-z\'\-.,]+)+)$', c)
            if f in ('car_number', 'driver') and m:
                rec.setdefault('car_number', m[1]); rec.setdefault('driver', m[2]); continue
            if f and f not in rec: rec[f] = c
            rec['cells'].append([h[2], c])
        if 'first' in rec or 'last' in rec:
            rec.setdefault('driver', ' '.join(x for x in (rec.get('first'), rec.get('last')) if x))
        d = rec.get('driver', '')
        if not re.search(r'[A-Za-z]{2}', d) or TIME.match(d): continue
        rec.update(line_no=n, section=section, raw=line.strip())
        rows.append(rec)
    return rows

NAME_RE = r"[A-Z][A-Za-z'.\-]+,?(?: [A-Za-z'.\-,()]+){1,4}?"
LINE = re.compile(r"^\s*(\d{1,3}T?m?)\s+(?:([A-Za-z][A-Za-z0-9]{0,7})\s+)?(\d{1,4}[a-z]?)\s+(" + NAME_RE + r")(?:\s{2,}(.*))?$")
LINE_NOPOS = re.compile(r"^\s*([A-Z][A-Za-z0-9]{0,5})\s+(\d{1,4})\s+(" + NAME_RE + r")\s{2,}(.*)$")   # RallyCross sheets: class, car, driver
TIMES = re.compile(r'\d{1,2}:\d{2}\.\d{2,3}(?:\+\w+)?|\d{1,4}\.\d{2,3}(?:\+\w+)?')
def flip(name):
    """"Example, Pat" -> "Pat Example" so one person matches across eras."""
    m = re.match(r'^([^,]+),\s*([^,()]+?)(\s*\(.*\))?$', name.strip())
    if not m or re.match(r'^(jr|sr|ii|iii|iv)\.?$', m[2].strip(), re.I) or ' ' in m[1].strip():
        return name.strip()          # "Pat Q Example, Jr." stays as it is
    return f"{m[2].strip()} {m[1].strip()}"
def parse_lines(text):
    rows, section, tot_col = [], '', None
    for n, line in enumerate(text.splitlines(), 1):
        st = line.strip()
        m = re.match(r"^'([^']+)'\s*-\s*total entries", st, re.I) or re.match(r"^([a-z0-9]{1,8})\s+-\s+'([^']+)'", st, re.I)
        if m: section = m[1]
        h = re.search(r'\bpax time\b', line, re.I) or re.search(r'\btotal\b', line, re.I)   # a Pax page ranks by pax time
        if h and not TIMES.search(line):
            tot_col = (h.start() + h.end()) / 2
            if m: pass
            continue
        if m: continue
        line = re.sub(r'^(\s*)\d{1,3}(\s+\d{1,3}T?m?\s+[A-Za-z])', r'\1\2', line)   # Pax pages: "Pax Pos." before "Pos."
        m = LINE.match(line)
        if m:
            pos, cls, num, name, rest, rest_at = m[1], m[2] or section, m[3], m[4], m[5] or '', m.start(5) if m[5] else len(line)
        else:
            m = LINE_NOPOS.match(line)
            if not m: continue
            pos, cls, num, name, rest, rest_at = None, m[1], m[2], m[3], m[4], m.start(4)
        ts = [(x.start() + rest_at, x.end() + rest_at, x.group()) for x in TIMES.finditer(rest)]
        if not ts: continue
        veh = rest[:ts[0][0] - rest_at].strip()
        if tot_col is not None:
            total = min(ts, key=lambda x: abs((x[0] + x[1]) / 2 - tot_col))[2]
        else:
            total = ts[0][2] if pos is None else ts[-1][2]
        rows.append(dict(position=pos, **{'class': cls}, car_number=num, driver=flip(name), vehicle=re.sub(r'\s{2,}', ' / ', veh) or None,
                         total=total, cells=[['times', ' '.join(x[2] for x in ts)]], line_no=n, section=section, raw=st))
    return rows
RNAME = r"[A-Z][A-Za-z'.\-]*(?: (?:[A-Z][A-Za-z'.\-]*|(?:von|van|de|la|du)[A-Za-z'.\-]*|Jr\.?|Sr\.?)){1,3}"   # "W Pat Example", "J Pat vonExample"
RTEAM = [re.compile(r"(" + RNAME + r")\s*[/&]\s*(" + RNAME + r")"),                  # "Example / von Sample"
         re.compile(r"(" + RNAME + r")\s{2,}(" + RNAME + r")(?=\s{2,}|\s+\d|$)")]         # two columns: driver, co-driver
RSHORT = re.compile(r"([A-Z][A-Za-z'.\-]+)\s*/\s*((?:(?:von|van|de|la|du) )?[A-Z][A-Za-z'.\-]+)")                  # "Example / Sample" (last names only)
CLS = re.compile(r'(?<![\w/])([ELSNUBCFXM]|SOP|Novice|Equipped|Limited|Stock|Unlimited|Open)(?![\w/])')
def parse_rally(text):
    """Road rally score sheets: one row per crew member with class, place and score, in the order printed."""
    rows, order, place_first, score_first, after = [], {}, False, False, []
    for n, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        if re.search(r'\b(driver|team|competitor|co-driver|navigator)\b', low) and not re.search(r'\d{2,}\.\d', low):
            heads = re.findall(r'\b(car|pos(?:ition)?|place|fin|o/a|total|points|score|driver|team|competitor)\b', low)
            names_at = next((i for i, h in enumerate(heads) if h in ('driver', 'team', 'competitor')), len(heads))
            place_first = any(h in ('pos', 'position', 'place', 'fin', 'o/a') for h in heads[:names_at])
            score_first = any(h in ('total', 'points', 'score') for h in heads[:names_at])
            after = [('place' if h in ('pos', 'position', 'place', 'fin', 'o/a') else 'score') for h in heads[names_at + 1:]
                     if h in ('pos', 'position', 'place', 'fin', 'o/a', 'total', 'points', 'score')]
            continue
        m = None
        for rx in RTEAM:
            m = rx.search(line)
            if m: break
        if not m:
            m = RSHORT.search(line)
        if not m or re.search(r'\b(results|rally|class|section|leg|total|page|region|scca)\b', m.group(0), re.I):
            continue
        if re.search(r'(?<!\d)\d{5}(?!\d)|\b(st|ave|rd|ct|dr|ln|lane|blvd|hwy|court|street|road|drive)\b\.?\s', line, re.I) and not re.search(r'\d+\.\d', line):
            continue                   # an address line, not a crew
        pre, post = line[:m.start()], line[m.end():]
        pre_n = re.findall(r'(?<![\w.])(\d{1,5}|DNF|DQ)(?![\w.])', pre)
        post_n = re.findall(r'(?<![\w.:/-])(-?\d{1,5}(?:\.\d+)?)(?![\w.:/])', post)
        c = CLS.search(pre) or CLS.search(post[:30])
        cls = c[1] if c else ''
        place = score = None
        if pre_n:
            if score_first and len(pre_n) >= 2: score = pre_n[1]
            if place_first and len(pre_n) >= 2: place = pre_n[1] if not score_first else None
        for what, val in zip(after, post_n):             # columns printed after the names, in header order
            if what == 'place' and place is None: place = val
            if what == 'score' and score is None: score = val
        if score is None and post_n and not after: score = post_n[0]
        if score is None: continue
        order[cls] = order.get(cls, 0) + 1
        place = place or str(order[cls])
        a, b = m.group(1).strip(), m.group(2).strip()
        for who, mate in ((a, b), (b, a)):
            rows.append({'position': place, 'class': cls, 'car_number': pre_n[0] if pre_n and pre_n[0].isdigit() else None,
                         'driver': who, 'vehicle': 'with ' + mate, 'total': score, 'cells': [['score', score]],
                         'line_no': n, 'section': cls, 'raw': line.strip()})
    return rows
PRIVATE_SHEET = re.compile(r'address|entry|entries|waiver|contact|registration|roster|member|phone|email|emergency', re.I)
def pick_sheets(sheets, title):
    """Results sheets only: never address lists, entry lists or waivers (they hold people's private details)."""
    sheets = [w for w in sheets if not PRIVATE_SHEET.search(title(w))]
    res = [w for w in sheets if re.search(r'result|score|standing|points|class|overall|pax|raw', title(w), re.I)]
    return res or sheets
def sheet_text(path, name=''):
    """A spreadsheet as text lines, cells three spaces apart, so the line readers can work on it."""
    import shutil, tempfile
    ext = os.path.splitext(name or path)[1].lower() or '.xlsx'
    if not path.lower().endswith(ext):          # the readers go by the extension; downloads are saved under their id
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False); tmp.close()
        shutil.copy(path, tmp.name)
        try:
            return sheet_text(tmp.name)
        finally:
            os.unlink(tmp.name)
    out = []
    try:
        if path.lower().endswith('.xls'):
            import xlrd
            for sh in xlrd.open_workbook(path).sheets():
                out += ['   '.join(str(v).strip() for v in sh.row_values(r)) for r in range(sh.nrows)]
        else:
            import openpyxl
            for ws in pick_sheets(openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets, lambda w: w.title):
                out += ['   '.join('' if v is None else str(v).strip() for v in row) for row in ws.iter_rows(values_only=True)]
    except Exception:
        try:                                    # an old .xls saved under a .xlsx name
            import xlrd
            for sh in pick_sheets(xlrd.open_workbook(path).sheets(), lambda w: w.name):
                out += ['   '.join(str(v).strip() for v in sh.row_values(r)) for r in range(sh.nrows)]
        except Exception:
            pass
    return '\n'.join(out)
def parse_html(path):
    """Results from an AXWare web page: read its table rows (a header row maps columns; class pages without one use
    AXWare's order: position, class, number, driver, car, color, runs, total)."""
    import html as H
    h = open(path, errors='ignore').read()
    cell = lambda c: H.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c))).strip()
    rows, header, section = [], None, ''
    for n, tr in enumerate(re.findall(r'(?is)<tr[^>]*>(.*?)</tr>', h), 1):
        cs = [cell(c) for c in re.findall(r'(?is)<t[dh][^>]*>(.*?)</t[dh]>', tr)]
        if not cs: continue
        if any(re.match(r'^driver$', x, re.I) for x in cs):
            header = [field(x) for x in cs]; continue
        m = re.match(r"^([a-z0-9]{1,8})\s+-\s+'([^']+)'", cs[0], re.I)
        if m:
            section, header = m[1], None; continue
        if not re.match(r'^\d{1,3}T?$', cs[0]) or len(cs) < 5: continue
        rec = {'cells': []}
        if header and len(header) == len(cs):
            for f, v in zip(header, cs):
                if f and v and f not in rec: rec[f] = v
                rec['cells'].append([f or '', v])
        else:
            rec.update(position=cs[0], car_number=cs[2], driver=cs[3], vehicle=cs[4] or None)
            rec['class'] = cs[1] or section
            times = [x for x in cs[5:] if re.match(r'^\d{1,4}\.\d{2,3}', x)]
            if times: rec['total'] = times[-1]
            rec['cells'] = [['times', ' '.join(times)]]
        if not re.search(r'[A-Za-z]{2}', rec.get('driver') or ''): continue
        rec.update(line_no=n, section=section, raw=' | '.join(cs))
        rows.append(rec)
    return rows
def zip_text(path):
    """Text of the results files inside a zip (PDF, web page, text or spreadsheet)."""
    import subprocess, tempfile, zipfile
    out = []
    try:
        with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as d:
            for i in z.infolist():
                n = i.filename.lower()
                if i.is_dir() or not n.endswith(('.pdf', '.htm', '.html', '.txt', '.xls', '.xlsx', '.csv')): continue
                p = z.extract(i, d)
                if n.endswith('.pdf'):
                    out.append(subprocess.run(['pdftotext', '-layout', '-q', p, '-'], capture_output=True, text=True).stdout)
                elif n.endswith(('.htm', '.html')): out.append(html_text(p))
                elif n.endswith(('.xls', '.xlsx')): out.append(sheet_text(p))
                else: out.append(open(p, errors='ignore').read())
    except Exception:
        pass
    return '\n'.join(out)
def zip_rows(path):
    """Results rows from web pages inside a zip (the HTML table reader; other members go through the text readers)."""
    import tempfile, zipfile
    rows = []
    try:
        with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as d:
            for i in z.infolist():
                if i.filename.lower().endswith(('.htm', '.html')): rows += parse_html(z.extract(i, d))
    except Exception:
        pass
    return rows
def html_text(path):
    h = open(path, errors='ignore').read()
    h = re.sub(r'(?is)<(script|style)\b.*?</\1\s*>', ' ', h)
    h = re.sub(r'(?i)</t[dh]>', '   ', h); h = re.sub(r'(?i)</tr>|<br\s*/?>|</p>|</h\d>', '\n', h)
    h = re.sub(r'<[^>]+>', ' ', h)
    import html as H
    return '\n'.join(re.sub(r'[ \t]{3,}', '     ', l) for l in H.unescape(h).splitlines())

os.makedirs(os.path.dirname(OUT), exist_ok=True)
tmp = OUT + '.tmp'
if os.path.exists(tmp): os.remove(tmp)
db = sqlite3.connect(tmp)
db.executescript('''
CREATE TABLE events (id INTEGER PRIMARY KEY, program TEXT, year INTEGER, date TEXT, name TEXT);
CREATE TABLE files (id INTEGER PRIMARY KEY, event_id INTEGER REFERENCES events(id), program TEXT, year INTEGER, date TEXT,
  kind TEXT, name TEXT, sorted_name TEXT, sorted_folder TEXT, original_folder TEXT, drive_url TEXT, original_drive_url TEXT,
  rows_found INTEGER, text TEXT);
CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE worker_points (id INTEGER PRIMARY KEY, file_id INTEGER REFERENCES files(id), snapshot_date TEXT, person_id INTEGER REFERENCES people(id),
  member_no TEXT, anniversary TEXT, name TEXT, carried_in REAL, used REAL, available REAL, by_event TEXT, line_no INTEGER, raw_line TEXT);
CREATE TABLE result_rows (id INTEGER PRIMARY KEY, file_id INTEGER REFERENCES files(id), event_id INTEGER REFERENCES events(id),
  person_id INTEGER REFERENCES people(id), program TEXT, year INTEGER, date TEXT, section TEXT, position TEXT, class TEXT,
  car_number TEXT, driver TEXT, vehicle TEXT, color TEXT, total TEXT, raw_time TEXT, pax_time TEXT, total_s REAL, raw_s REAL, pax_s REAL, cells TEXT, line_no INTEGER, raw_line TEXT);
''')
def event_name(text, name):
    for line in text.splitlines()[:25]:
        m = re.search(r'-\s*(?:#\d+\s*-\s*)?(.+?)\s*-\s*(?:mon|tue|wed|thu|fri|sat|sun)\w*\s+\d', line, re.I)
        if m: return m[1].strip(' -')
    n = re.sub(r'\.\w+$', '', name)
    n = re.sub(r'^\d{9,}_\d+_FT\d+_', '', n)
    n = re.sub(r'[_\-]+', ' ', n)
    n = re.sub(r'\b(\d{1,4}[ /.]?){2,3}\b|\b(final|results?|class|pax|raw|index|overall|by|for|web|corrected|revised|r\d)\b', ' ', n, flags=re.I)
    return ' '.join(n.split()).title() or name
events, drivers = {}, {}
def prog_is_wp(m): return m['program'] == 'Worker points'
def num(v):
    m = re.match(r'^\s*(\d{1,4}\.\d{1,4})', v or '')
    return float(m[1]) if m else None
for i, (fid, p) in enumerate(plan.items(), 1):
    m = meta[fid]
    t_path = os.path.join(TEXT, fid + '.txt')
    low = m['name'].lower(); raw = f'{WORK}/files/{fid}'
    if os.path.exists(t_path): text = open(t_path, errors='ignore').read()
    elif low.endswith(('.htm', '.html')): text = html_text(raw)
    elif low.endswith(('.txt', '.csv')): text = open(raw, errors='ignore').read()
    elif low.endswith(('.xls', '.xlsx')): text = sheet_text(raw, m['name'])
    elif low.endswith('.zip'): text = zip_text(raw)
    else: text = fallback.get(fid, '')
    year = int(m['year']) if m['year'] else None
    ev = None
    if m['program'] not in ('Worker points', 'Not sorted', 'Other') and m['kind'] == 'event':
        key = (m['program'], m['date'] or f"{year}-?-{event_name(text, m['name'])}")
        if key not in events:
            events[key] = len(events) + 1
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (events[key], m['program'], year, m['date'] or None, event_name(text, m['name'])))
        ev = events[key]
    rows = parse_html(raw) if low.endswith(('.htm', '.html')) else zip_rows(raw) if low.endswith('.zip') else []
    if m['program'] == 'Rally' and low.endswith(('.xls', '.xlsx')):
        rows, text = [], ''          # rally workbooks mix entry lists and sheets this reader gets wrong: link the file only
    if m['program'] == 'Rally' and text and not rows: rows = parse_rally(text)   # crews: driver and navigator
    if not rows and text: rows = parse(text)
    if not rows and text: rows = parse_lines(text)
    db.execute('INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (i, ev, m['program'], year, m['date'] or None, m['kind'], m['name'],
        p['name'], p['folder'], m['oldpath'], f"https://drive.google.com/file/d/{copies.get(fid, fid)}/view",
        f'https://drive.google.com/file/d/{fid}/view', len(rows), text))
    for r in rows:
        d = flip(' '.join(r['driver'].split()).strip(' ,')) if ',' in r['driver'].strip(' ,') else ' '.join(r['driver'].split()).strip(' ,')
        if prog_is_wp(m):
            d = ' '.join(x for x in (r.get('first'), r.get('last')) if x) or d
        if d.lower() not in drivers:
            drivers[d.lower()] = len(drivers) + 1
            db.execute('INSERT INTO people VALUES (?,?)', (drivers[d.lower()], d.title() if d.isupper() else d))
        pid = drivers[d.lower()]
        if prog_is_wp(m):
            got = {'carried_in': None, 'used': None, 'available': None}; ev_pts = {}
            for lab, val in r['cells']:
                l = lab.lower()
                if not re.match(r'^-?\d+(\.\d+)?$', val): continue
                if re.search(r'\d{1,2}/\d{1,2}', l): got['carried_in'] = float(val)
                elif re.match(r'^(19|20)\d\d$', l) or 'used' in l: got['used'] = float(val)
                elif 'avail' in l: got['available'] = float(val)
                else: ev_pts[lab] = float(val)
            db.execute('INSERT INTO worker_points (file_id,snapshot_date,person_id,member_no,anniversary,name,carried_in,used,available,by_event,line_no,raw_line) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (i, m['date'] or None, pid, r.get('member_no'), r.get('anniversary'), d, got['carried_in'], got['used'], got['available'], json.dumps(ev_pts), r['line_no'], r['raw']))
            continue
        db.execute('INSERT INTO result_rows (file_id,event_id,person_id,program,year,date,section,position,class,car_number,driver,vehicle,color,total,raw_time,pax_time,total_s,raw_s,pax_s,cells,line_no,raw_line) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (i, ev, pid, m['program'], year, m['date'] or None, r['section'], r.get('position'), r.get('class'), r.get('car_number'), d,
             r.get('vehicle'), r.get('color'), r.get('total'), r.get('raw_time'), r.get('pax_time'), num(r.get('total')), num(r.get('raw_time')), num(r.get('pax_time')),
             json.dumps(r['cells']), r['line_no'], r['raw']))
db.executescript('''
CREATE INDEX rr_person ON result_rows(person_id); CREATE INDEX wp_person ON worker_points(person_id); CREATE INDEX rr_event ON result_rows(event_id); CREATE INDEX rr_file ON result_rows(file_id);
CREATE INDEX f_event ON files(event_id);
CREATE VIRTUAL TABLE files_fts USING fts5(name, text, content='files', content_rowid='id');
INSERT INTO files_fts(rowid, name, text) SELECT id, name, text FROM files;
CREATE VIRTUAL TABLE people_fts USING fts5(name, content='people', content_rowid='id');
INSERT INTO people_fts(rowid, name) SELECT id, name FROM people;
''')
db.commit(); db.execute('VACUUM'); db.close(); os.replace(tmp, OUT)
c = sqlite3.connect(OUT)
for q in ['select count(*) from files', 'select count(*) from events', 'select count(*) from result_rows', 'select count(*) from people', 'select count(*) from worker_points',
          "select program, count(*), sum(rows_found>0) from files group by program"]:
    print(q, '->', c.execute(q).fetchall())
