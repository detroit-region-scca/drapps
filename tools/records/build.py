"""Build scca.db: every DRSCCA results file, its text, and every table row found in it.

Inputs (in $WORK): plan.json, sorted.csv, copied.json from the Drive sort, files/<drive id>, and text/<id>.txt
(pdftotext -layout). Output: data/scca.db (SQLite, opened read-only by Datasette).
"""
import csv, json, os, re, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import axware
WORK, OUT = sys.argv[1], sys.argv[2]
TEXT = os.path.join(os.path.dirname(OUT), 'text')   # pdftotext / OCR output, next to the db
plan = {p['id']: p for p in json.load(open(f'{WORK}/plan.json'))}
meta = {r['id']: r for r in csv.DictReader(open(f'{WORK}/sorted.csv'))}
copies = json.load(open(f'{WORK}/copied.json'))
_cd = os.environ.get('CURATED_DIR', '')
SPLITS = {}                                   # '<drive id>__dawnbuster2' -> '<drive id>'
if _cd and os.path.isdir(os.path.join(_cd, 'results')):      # extra events transcribed out of one file: build them as files of their own
    for _f in sorted(os.listdir(os.path.join(_cd, 'results'))):
        _id = _f[:-5]
        if _f.endswith('.json') and _id not in meta and '__' in _id and _id.rsplit('__', 1)[0] in meta:
            SPLITS[_id] = _id.rsplit('__', 1)[0]
            meta[_id] = dict(meta[SPLITS[_id]]); plan[_id] = dict(plan[SPLITS[_id]])
fallback = json.load(open(f'{WORK}/text.json'))

LABELS = [  # (field, header patterns) first match wins
    ('position', r'^(pos\.?|fin|place|oa|overall|p)$'), ('class', r'^(class|cls|cla)$'),
    ('car_number', r'^(#|car ?#|no\.?|number|car no\.?)$'), ('driver', r'^(driver|name|competitor.*|driver/navigator|team)$'),
    ('first', r'^first ?name$'), ('last', r'^last ?name$'), ('vehicle', r'^(car model|vehicle|car|yr make model.*|make model|model)$'),
    ('color', r'^(car )?color$'), ('pax_time', r'^pax ?time$'), ('raw_time', r'^raw ?time$'), ('total', r'^(total|total ?time|best|score)$'),
]
HEAD_WORDS = re.compile(r'\b(driver|class|pos\.?|name|total|vehicle|car)\b', re.I)
TIME = re.compile(r'^-?\d{1,4}\.\d{1,4}')

COLOR = re.compile(r'(?i)^((light|dark|bright|metallic|pearl|met\.?)\s+)?(red|blue|black|white|silver|green|gr[ae]y|yellow|orange|purple|gold|tan|brown|maroon|burgundy|beige|pink|teal|bronze|champagne|navy)'
                   r'(\s*(/|&|and|-|w/)\s*\w+)*$')
MAKES = re.compile(r'\b(Acura|Alfa|Audi|BMW|Buick|Cadillac|Chevrolet|Chevy|Chrysler|Corvette|Datsun|Dodge|Ferrari|Fiat|Ford|Honda|Hyundai|Infiniti|Jeep|Kia|Lexus|Lotus|Mazda|'
                   r'Mercedes|Miata|Mini|Mitsubishi|Mustang|Nissan|Plymouth|Pontiac|Porsche|Saab|Saturn|Scion|Subaru|Toyota|Volkswagen|VW|Volvo)\b')


def car_out_of_name(r):
    """Reader rows only (transcriptions are right as written): a car colour is never the car, and a long name that ran
    into the car column ("Pat Q Example BMW M2") is split back into driver and car."""
    r = dict(r)
    if r.get('vehicle') and COLOR.match(r['vehicle'].strip()):
        r['color'], r['vehicle'] = r.get('color') or r['vehicle'], None
    words = r['driver'].split()
    for k, w in enumerate(words):
        if k >= 2 and MAKES.fullmatch(w) and ',' not in r['driver'] and not (k == len(words) - 1 and w in ('Ford', 'Mini', 'Lotus', 'Saturn', 'Kia', 'Jeep')):   # a surname
            car = re.sub(r'(\s+\d+\.\d+)+$', '', ' '.join(words[k:]))            # times that ran in after the car
            car = ' '.join(x for x in car.split() if not COLOR.match(x))
            r['driver'] = ' '.join(words[:k])
            r['vehicle'] = r.get('vehicle') or car
            break
    return r

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
        if not m or re.search(r'\b(results|rally|class|sect|section|leg|total|page|region|scca|control|checkpoint)\b', m.group(0), re.I):
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
OFF_NAME = re.compile(r"^\s*([A-Z][A-Za-z'.\-]+(?: [A-Za-z'.\-]+){0,3})\s{2,}\S")       # "Pat Example    Hometown ST   12345   DET"
OFF_PLACE = re.compile(r'^\s*(\d+|DNF|DNS|DQ)\s{2,}([A-Z]{1,3})(\d+)\b(?:\s{2,}(\d{1,4})\b)?')   # "  1      E1      3   ..."
def parse_scca_official_pdf(text):
    """SCCA's road rally "Official Results" sheet saved as a PDF (2023 on): each crew is the driver's line, the finish
    line (overall place, class letter and class place, car #, vehicle, sometimes the score), the score, then the
    navigator's line. The name lines also print hometown, member # and region: only the names are kept."""
    if not re.search(r'Team Members', text) or not re.search(r'Hometown', text):
        return []
    lines = text.splitlines()
    start = next((n for n, l in enumerate(lines) if 'Finish Positions' in l), None)
    if start is None:
        return []
    kinds = []                                  # ('name', n, name) / ('place', n, oa, cls, place, car, score)
    for n in range(start + 1, len(lines)):
        l = lines[n]
        p = OFF_PLACE.match(l)
        if p:
            sc = re.search(r'(\d+\.\d+)\s*$', l)
            kinds.append(['place', n, p[1], p[2], p[3], p[4], sc[1] if sc else None])
            continue
        m = OFF_NAME.match(l)
        if m and not re.search(r'(?i)worker|first last|team members|hometown|member #|signature', l):
            kinds.append(['name', n, ' '.join(m[1].split())])
            continue
        sc = re.fullmatch(r'\s*(\d+\.\d+)\s*', l)
        if sc and kinds and kinds[-1][0] == 'place' and not kinds[-1][6]:
            kinds[-1][6] = sc[1]
    rows = []
    for i, k in enumerate(kinds):
        if k[0] != 'place':
            continue
        before = kinds[i - 1][2] if i and kinds[i - 1][0] == 'name' else ''
        after = kinds[i + 1][2] if i + 1 < len(kinds) and kinds[i + 1][0] == 'name' else ''
        _, n, oa, cls, place, car, score = k
        for who, other in ((before, after), (after, before)):
            if who:
                rows.append({'position': place, 'class': cls, 'car_number': car, 'driver': who,
                             'vehicle': ('with ' + other) if other else None, 'total': score,
                             'cells': [['score', score or '']], 'line_no': n + 1, 'section': cls, 'raw': f'overall {oa}'})
    return rows
PRIVATE_SHEET = re.compile(r'address|entry|entries|waiver|contact|registration|roster|member|phone|email|emergency', re.I)
def pick_sheets(sheets, title):
    """Results sheets only: never address lists, entry lists or waivers (they hold people's private details)."""
    sheets = [w for w in sheets if not PRIVATE_SHEET.search(title(w))]
    res = [w for w in sheets if re.search(r'result|score|standing|points|class|overall|pax|raw', title(w), re.I)]
    return res or sheets
def parse_scca_template(path, name=''):
    """SCCA's road rally "Official Results" workbook: one row per driver (overall place, class place under the class
    letter, first, last, car #, vehicle, score) with the navigator on the next row. Only names, class, place, car and
    score are kept; hometowns and member numbers in the sheet are never read into the rows."""
    import shutil, tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=os.path.splitext(name or path)[1].lower() or '.xlsx', delete=False); tmp.close()
    shutil.copy(path, tmp.name)
    rows = []
    try:
        import openpyxl
        wb = openpyxl.load_workbook(tmp.name, read_only=True, data_only=True)
        for ws in wb.worksheets:
            if PRIVATE_SHEET.search(ws.title) or not re.search(r'(?i)official|result', ws.title):
                continue
            grid = [list(r) for r in ws.iter_rows(values_only=True)]
            for hi, head in enumerate(grid):
                labels = [str(c).strip() if c is not None else '' for c in head]
                if 'O/A' not in labels or 'First' not in labels or 'Last' not in labels:
                    continue
                oa, first, last = labels.index('O/A'), labels.index('First'), labels.index('Last')
                classes = {i: labels[i] for i in range(oa + 1, first) if labels[i]}
                after = lambda lab: next((i for i in range(last, len(labels)) if labels[i] == lab), None)
                car, score = after('Car #'), after('Score')
                txt = lambda r, i: '' if i is None or i >= len(r) or r[i] is None else str(r[i]).strip()
                for n in range(hi + 1, len(grid) - 1):
                    r, nav = grid[n], grid[n + 1]
                    if not re.fullmatch(r'\d+', txt(r, oa)) or not re.search(r'[A-Za-z]{2}', txt(r, last)):
                        continue              # empty template rows hold numbers where the names go
                    cls, place = next(((lab, txt(r, i)) for i, lab in classes.items() if re.fullmatch(r'\d+', txt(r, i))), ('', txt(r, oa)))
                    driver = f"{txt(r, first)} {txt(r, last)}".strip()
                    mate = f"{txt(nav, first)} {txt(nav, last)}".strip() if not txt(nav, oa) and re.search(r'[A-Za-z]{2}', txt(nav, last)) else ''
                    sc = re.sub(r'\.0$', '', txt(r, score))
                    for who, other in ((driver, mate), (mate, driver)):
                        if who:
                            rows.append({'position': place, 'class': cls, 'car_number': txt(r, car) or None, 'driver': who,
                                         'vehicle': ('with ' + other) if other else None, 'total': sc or None,
                                         'cells': [['score', sc]], 'line_no': n + 1, 'section': cls, 'raw': f'overall {txt(r, oa)}'})
                break
    except Exception:
        pass
    finally:
        os.unlink(tmp.name)
    return rows
def cell(v):
    """A spreadsheet value as the sheet shows it: whole numbers without ".0"."""
    if v is None: return ''
    if isinstance(v, float) and v.is_integer(): return str(int(v))
    return str(v).strip()
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
            for sh in pick_sheets(xlrd.open_workbook(path).sheets(), lambda w: w.name):
                out += ['   '.join(cell(v) for v in sh.row_values(r)) for r in range(sh.nrows)]
        else:
            import openpyxl
            for ws in pick_sheets(openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets, lambda w: w.title):
                out += ['   '.join(cell(v) for v in row) for row in ws.iter_rows(values_only=True)]
    except Exception:
        try:                                    # an old .xls saved under a .xlsx name
            import xlrd
            for sh in pick_sheets(xlrd.open_workbook(path).sheets(), lambda w: w.name):
                out += ['   '.join(cell(v) for v in sh.row_values(r)) for r in range(sh.nrows)]
        except Exception as e:
            if os.environ.get('BUILD_DEBUG'): print('sheet_text failed:', path, repr(e))
    return '\n'.join(out)
def parse_grid(text):
    """Spreadsheet text (cells joined by three spaces, one row per line): match cells to the header row by column
    number. An unlabeled number column before the names is the place."""
    rows, header, section = [], None, ''
    for n, line in enumerate(text.splitlines(), 1):
        cs = [c.strip() for c in line.split('   ')]
        if not any(cs):
            continue
        labels = [field(c) if c else None for c in cs]
        if ('driver' in labels or ('first' in labels and 'last' in labels)) and not any(TIME.match(c) for c in cs if c):
            header = labels; continue
        if header is None:
            continue
        rec = {'cells': []}
        for i, v in enumerate(cs):
            if not v: continue
            f = header[i] if i < len(header) else None
            if f is None and i == 0 and re.fullmatch(r'\d{1,3}T?', v): f = 'position'
            if f and f not in rec: rec[f] = v
            rec['cells'].append([f or '', v])
        if 'first' in rec or 'last' in rec:
            rec['driver'] = ' '.join(x for x in (rec.get('first'), rec.get('last')) if x)
        if not re.search(r'[A-Za-z]{2}', rec.get('driver') or ''):
            continue
        if 'total' not in rec:          # no Total column: the first time after the car
            t = next((v for v in cs if re.fullmatch(r'\d{1,4}\.\d{1,3}', v)), None)
            if t: rec['total'] = t
        rec.update(line_no=n, section=rec.get('class') or section, raw=' | '.join(c for c in cs if c))
        rows.append(rec)
    return rows
def parse_stacked(text):
    """Early RallyCross sheets that stack each entry on three lines (name; car # and times; car), or put
    "Name/ car" on one line with the car # on the next. Total = the last time (or DNF/NS)."""
    lines = [l for l in text.splitlines() if l.strip()]
    rows, cls = [], ''
    NAME_ONLY = re.compile(r"^\s*([A-Z][A-Za-z'.\-]+(?: [A-Z][A-Za-z'.\-]+){1,2})\s*$")
    NUMS = re.compile(r"^\s*(\d{1,4})\s+((?:\d{1,3}\.\d{1,3}|DNF|NS|DQ)(?:\s+(?:\d{1,3}\.\d{1,3}|DNF|NS|DQ|\d))*)\s*$")
    SLASH = re.compile(r"^\s*([A-Z][A-Za-z'.\-]+(?: [A-Z][A-Za-z'.\-]+){1,2})\s*/\s*(.*?)\s{2,}((?:\d{1,3}\.\d{1,3}|DNF).*)$")
    i = 0
    while i < len(lines):
        l = lines[i]
        if re.fullmatch(r'\s*[A-Z]{1,3}\d?\s*', l):
            cls = l.strip(); i += 1; continue
        m = NAME_ONLY.match(l)
        if m:
            j = i + 1
            while j < len(lines) and re.fullmatch(r'\s*[A-Z]{1,3}\d?\s*', lines[j]):
                cls = lines[j].strip(); j += 1
            n = NUMS.match(lines[j]) if j < len(lines) else None
            if n:
                vals = n[2].split()
                car_line = lines[j + 1].strip() if j + 1 < len(lines) and not re.search(r'\d{2}\.\d', lines[j + 1]) and not NAME_ONLY.match(lines[j + 1]) else ''
                tot = next((v for v in reversed(vals) if re.fullmatch(r'\d+\.\d+|DNF|NS|DQ', v)), None)
                rows.append(dict(position=None, **{'class': cls}, car_number=n[1], driver=m[1], vehicle=car_line or None, total=tot,
                                 cells=[['times', n[2]]], line_no=i + 1, section=cls, raw=f'{m[1]} {n[1]} {n[2]}'))
                i = j + (2 if car_line else 1); continue
        m = SLASH.match(l)
        if m:
            car_no = lines[i + 1].strip() if i + 1 < len(lines) and re.fullmatch(r'\s*\d{1,4}\s*', lines[i + 1]) else None
            ts = re.findall(r'\d{1,3}\.\d{1,3}|DNF', m[3])
            rows.append(dict(position=None, **{'class': cls}, car_number=car_no, driver=m[1], vehicle=m[2].strip() or None,
                             total=min((t for t in ts if t != 'DNF'), key=float, default=None), cells=[['times', ' '.join(ts)]],
                             line_no=i + 1, section=cls, raw=l.strip()))
        i += 1
    return rows
RXLINE = re.compile(r"^\s*(?P<cls>[A-Z]{1,4}\d?)\s+(?P<car>\d{1,4})\s+(?:(?P<reg>[A-Z]{2,4})\s+)?(?:\(\d+\)\s+)?(?:[T*]\s+)?"
                    r"(?P<first>[A-Z][A-Za-z'.\-]*(?: [A-Z][A-Za-z'.\-]*)?)\s{2,}(?P<last>[A-Z][A-Za-z'.\-]*(?:[ -][A-Za-z'.\-]+)?)"
                    r"(?:\s{2,}|\s+(?=\d{2,4}\s))(?P<veh>.*?)\s*(?:\*\s*)?\(?\s*(?P<total>\d{2,4}\.\d{1,3}|DNF|DNS)\s*\)?\s+(?P<runs>.*)$")
def parse_rx(text):
    """RallyCross sheets (2009 on): Class, Car #, Reg, T, First Name, Last Name, car, Total, then each run and its
    penalty. Places are by total within each class (lowest first), the way the sheets are sorted."""
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        m = RXLINE.match(line)
        if not m or not re.search(r'[a-z]', m['first'] + m['last']):
            continue
        runs = re.findall(r'\d{1,3}\.\d{1,3}', m['runs'])
        join = lambda x: ' '.join(re.sub(r'\b([A-Z]) (?=[a-z]{2,})', r'\1', x).split())   # "M itsubishi" -> "Mitsubishi"
        rows.append(dict(**{'class': m['cls']}, car_number=m['car'], driver=join(f"{m['first']} {m['last']}"), vehicle=join(m['veh'].strip(' *')) or None,
                         total=m['total'], cells=[['runs', ' '.join(runs)]], line_no=n, section=m['cls'], raw=line.strip()))
    for cls in {r['class'] for r in rows}:
        ranked = sorted((r for r in rows if r['class'] == cls), key=lambda r: float(r['total']) if r['total'][0].isdigit() else 1e9)
        for i, r in enumerate(ranked, 1):
            r['position'] = str(i) if r['total'][0].isdigit() else r['total']
    return rows
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
  rows_found INTEGER, text TEXT, ocr TEXT, sheet_label TEXT);
CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE worker_points (id INTEGER PRIMARY KEY, file_id INTEGER REFERENCES files(id), snapshot_date TEXT, person_id INTEGER REFERENCES people(id),
  member_no TEXT, anniversary TEXT, name TEXT, carried_in REAL, used REAL, available REAL, by_event TEXT, line_no INTEGER, raw_line TEXT);
CREATE TABLE result_rows (id INTEGER PRIMARY KEY, file_id INTEGER REFERENCES files(id), event_id INTEGER REFERENCES events(id),
  person_id INTEGER REFERENCES people(id), program TEXT, year INTEGER, date TEXT, section TEXT, position TEXT, class TEXT,
  car_number TEXT, driver TEXT, vehicle TEXT, color TEXT, total TEXT, raw_time TEXT, pax_time TEXT, total_s REAL, raw_s REAL, pax_s REAL, cells TEXT, line_no INTEGER, raw_line TEXT,
  class_place TEXT, laps TEXT, best_lap TEXT, status TEXT);
''')
def event_name(text, name):
    rx = re.search(r'Round\s+\d+\s*-\s*[A-Za-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\s+(.+?)\s*$', text[:1500], re.M)
    if rx:
        return re.sub(r',\s*[A-Z][A-Za-z .]+,\s*(MI|OH|IN|ON)\b.*$', '', rx[1]).strip(' -,')
    for line in text.splitlines()[:25]:
        m = re.search(r'-\s*(?:#\d+\s*-\s*)?(.+?)\s*-\s*(?:mon|tue|wed|thu|fri|sat|sun)\w*\s+\d', line, re.I)
        if m: return m[1].strip(' -')
    n = re.sub(r'\.\w+$', '', name)
    n = re.sub(r'^\d{9,}_\d+_FT\d+_', '', n)
    n = re.sub(r'[_\-]+', ' ', n)
    n = re.sub(r'\b(\d{1,4}[ /.]?){2,3}\b|\b(final|results?|class|pax|raw|index|overall|by|for|web|corrected|revised|r\d)\b', ' ', n, flags=re.I)
    return ' '.join(n.split()).title() or name
def parse_speedhive(raw):
    """A Speedhive session CSV made by speedhive_import.py: every column kept. A DNS/DNF/DQ car shows its status
    instead of a place, and its zero time is left out."""
    import csv as _csv
    rows = []
    lines = list(_csv.reader(open(raw, errors='ignore')))
    head = next((i for i, r in enumerate(lines) if r[:2] == ['Pos', 'Class']), None)
    if head is None:
        return []
    cols = {h: i for i, h in enumerate(lines[head])}
    for n, r in enumerate(lines[head + 1:], head + 2):
        g = lambda h: (r[cols[h]].strip() if h in cols and cols[h] < len(r) else '')  # noqa: E731
        if not g('Driver'):
            continue
        st = g('Status')
        tot = g('Total time')
        if st or re.fullmatch(r'[0:.]*', tot or '0'):
            tot = '' if re.fullmatch(r'[0:.]*', tot or '0') else tot
        rows.append({'position': st or g('Pos') or None, 'class': g('Class'), 'section': g('Class'), 'car_number': g('#') or None,
                     'driver': g('Driver'), 'total': tot or None, 'class_place': None if st else (g('In class') or None),
                     'laps': g('Laps') or None, 'best_lap': None if re.fullmatch(r'[0:.]*', g('Best lap') or '0') else g('Best lap'),
                     'status': st or None, 'cells': [], 'line_no': n, 'raw': ','.join(r)})
    return rows
CURATED = os.environ.get('CURATED_DIR', '')
def curated(fid):
    """A transcription of this sheet (CURATED_DIR/results/<drive id>.json, made by reading the original and checked by a
    second pass): it replaces the automatic reading. Rally rows are crews (driver and partner)."""
    p = os.path.join(CURATED, 'results', fid + '.json') if CURATED else ''
    return json.load(open(p)) if p and os.path.exists(p) else None
def curated_rows(cur, program):
    rows = []
    overall = program == 'Road Racing' or re.match(r'(?i)(pax|raw)', cur.get('label') or '')
    for n, r in enumerate(cur.get('rows', []), 1):
        if overall:        # a race's finishing order, a PAX or raw sheet: the overall place; class place beside it
            place = str(r.get('overall_place') or r.get('status') or r.get('class_place') or '').strip() or None   # a classified "not running" car keeps its place
        else:
            place = str(r.get('class_place') or r.get('status') or r.get('overall_place') or '').strip() or None
        base = {'position': place, 'class_place': (str(r.get('class_place') or '') or None) if overall else None, 'status': r.get('status') or None, 'class': r.get('class') or '', 'car_number': str(r.get('car') or '') or None, 'total': str(r.get('score') if r.get('score') is not None else '') or None,
                'raw_time': r.get('raw') or None, 'pax_time': r.get('pax') or None, 'section': r.get('class') or '', 'line_no': n,
                'laps': str(r.get('laps') or '') or None, 'best_lap': str(r.get('best') or '') or None,
                'cells': [['overall', str(r.get('overall_place') or '')], ['status', r.get('status') or '']], 'raw': 'transcribed'}
        crew = [x.strip() for x in [r.get('driver') or ''] + (r.get('partner') or '').split(' & ') if x.strip()]
        placings = [base] + [dict(base, position=str(a.get('place') or ''), class_place=str(a.get('place') or ''), **{'class': a.get('class') or ''},
                                  section=a.get('class') or '', line_no=n + 10000 * k) for k, a in enumerate(r.get('also') or [], 1)]
        for pl in placings:          # a Novice crew is also placed in its car class ("S3 N1"): listed in both classes
            for who in crew:
                other = [x for x in crew if x is not who]
                rows.append(dict(pl, driver=who, vehicle=r.get('vehicle') or None))     # the car as printed (rally crews share it)
    return rows
OCR_NAME = re.compile(r"[A-Z][A-Za-z'.\-]+(?: (?:[A-Z][A-Za-z'.\-]*|(?:von|van|de|la|du)[A-Za-z'.\-]*|Jr\.?|Sr\.?|II|III)){1,3}")
def ocr_gate(rows):
    """Rows read from a scan are kept only when the scan read cleanly: a real name and a readable number on at least 80%
    of them (wrong results are worse than a link to the scan). Returns (rows, note)."""
    if not rows:
        return rows, ''
    def ok(r):
        nums = [r.get(k) for k in ('total', 'raw_time', 'pax_time', 'position') if r.get(k)]
        return bool(OCR_NAME.fullmatch(' '.join((r.get('driver') or '').split()))) and any(
            re.fullmatch(r'\d{1,4}(?:[.:]\d{1,3})*[A-Za-z]?|DNF|DNS|DQ|DSQ', str(v).strip()) for v in nums)
    good = sum(1 for r in rows if ok(r))
    if good < 0.8 * len(rows):
        return [], f'scan not read: only {good} of {len(rows)} lines read cleanly'
    return [r for r in rows if ok(r)], f'read from a scan ({good} of {len(rows)} lines kept)'
events, drivers = {}, {}
def prog_is_wp(m): return m['program'] == 'Worker points'
def num(v):
    m = re.match(r'^\s*(\d{1,4}\.\d{1,4})', v or '')
    return float(m[1]) if m else None
for i, (fid, p) in enumerate(plan.items(), 1):
    m = meta[fid]
    src_id = SPLITS.get(fid, fid)             # a second event transcribed out of the same file ("<id>__dawnbuster2")
    t_path = os.path.join(TEXT, src_id + '.txt')
    low = m['name'].lower(); raw = f'{WORK}/files/{src_id}'
    if os.path.exists(t_path): text = open(t_path, errors='ignore').read()
    elif low.endswith(('.htm', '.html')): text = html_text(raw)
    elif low.endswith('.csv'):
        import csv as _csv
        text = '\n'.join('   '.join(c.strip() for c in row) for row in _csv.reader(open(raw, errors='ignore')))
    elif low.endswith('.txt'): text = open(raw, errors='ignore').read()
    elif low.endswith(('.xls', '.xlsx')): text = sheet_text(raw, m['name'])
    elif low.endswith('.zip'): text = zip_text(raw)
    else: text = fallback.get(fid, '')
    cur = curated(fid)
    ax_note = None
    if cur:
        ce = cur.get('event') or {}
        if ce.get('date'):
            m = dict(m, date=ce['date'], year=ce['date'][:4])
        elif ce.get('year'):                          # a re-posted old sheet with no printed date: its own year, never the re-post date
            m = dict(m, date=None, year=str(ce['year']))
        if ce.get('program'):
            m = dict(m, program=ce['program'])          # filed under the wrong program
        if cur.get('use') is False:
            m = dict(m, kind='not results')          # not a results sheet (a photo, a fact sheet, a duplicate): kept only as a file
    year = int(m['year']) if m['year'] else None
    ev = None
    if m['program'] not in ('Worker points', 'Not sorted', 'Other') and m['kind'] == 'event':
        ename = ((cur or {}).get('event') or {}).get('name') or event_name(text, m['name'])
        key = (m['program'], m['date'] or f"{year}-?-{ename}") if not (cur and (cur.get('event') or {}).get('name')) else (m['program'], m['date'] or year, ename)
        if key not in events:
            events[key] = len(events) + 1
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)', (events[key], m['program'], year, m['date'] or None, ename))
        ev = events[key]
    rows = parse_html(raw) if low.endswith(('.htm', '.html')) else zip_rows(raw) if low.endswith('.zip') else []
    if cur is not None and cur.get('keep_reader_rows'):
        cur = None                               # a correction of the event only (name, date): the sheet is still read below
    if cur is not None:
        rows = curated_rows(cur, m['program']) if cur.get('use') is not False else []
    elif fid.startswith('speedhive-'):
        rows = parse_speedhive(raw)
    elif m['program'] in ('Autocross', 'RallyCross') and m['kind'] == 'event' and text and not low.endswith(('.htm', '.html')):
        ax_rows, ax_kind, ax_exp = axware.read(text)       # AXWare sheets: the strict reader, checked against the sheet's own count
        if ax_kind:
            ax_rows = axware.dedupe(ax_rows)
            ok, check_note = axware.check(ax_rows, ax_kind, ax_exp)
            rows = ax_rows if ok else []
            ax_note = ('read: ' if ok else 'needs transcription: ') + check_note
    if cur is None and m['program'] == 'Rally' and low.endswith(('.xls', '.xlsx')):
        rows, text = parse_scca_template(raw, m['name']), ''   # rally workbooks: only SCCA's official results template
    if cur is None and ax_note is None and m['program'] == 'Rally' and text and not rows: rows = parse_scca_official_pdf(text)   # SCCA official results PDF
    if cur is None and ax_note is None and m['program'] == 'Rally' and text and not rows: rows = parse_rally(text)   # crews: driver and navigator
    if cur is None and ax_note is None and not rows and text and (low.endswith(('.xls', '.xlsx', '.csv')) or low.endswith('.zip')):
        g = parse_grid(text)          # keep it only when it found real results (a place or a time on most rows)
        if g and sum(1 for r in g if r.get('position') or r.get('total')) >= 0.5 * len(g): rows = g
    if cur is None and ax_note is None and not rows and text and m['program'] == 'RallyCross':
        rx = parse_rx(text)          # the RallyCross layout, when it covers the sheet
        if len(rx) >= 5: rows = rx
    if cur is None and ax_note is None and not fid.startswith('speedhive-') and not rows and text: rows = parse(text)
    if cur is None and ax_note is None and not fid.startswith('speedhive-') and not rows and text: rows = parse_lines(text)
    if cur is None and ax_note is None and not rows and text and m['program'] == 'RallyCross': rows = parse_stacked(text)
    ocr_note = ax_note
    if cur is None and m['program'] in ('Autocross', 'RallyCross') and m['kind'] == 'event' and rows and ax_note is None:
        k2 = 'class' if any(r.get('total') for r in rows) else 'raw'
        ok, check_note = axware.check(rows, k2, None)             # other readers: places must agree with times
        if not ok:
            rows, ocr_note = [], 'needs transcription: ' + check_note
    if cur is None and os.path.exists(os.path.join(TEXT, fid + '.ocr.json')) and not prog_is_wp(m):     # text from a scan: gate it
        rows, ocr_note = ocr_gate(rows)
    db.execute('INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (i, ev, m['program'], year, m['date'] or None, m['kind'], m['name'],
        p['name'], p['folder'], m['oldpath'], f"https://drive.google.com/file/d/{copies.get(src_id, src_id)}/view",
        f'https://drive.google.com/file/d/{src_id}/view', len(rows), text, ocr_note if cur is None else 'transcribed',
        (cur or {}).get('label')))
    for r in rows:
        if cur is None and not prog_is_wp(m):
            r = car_out_of_name(r)
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
        db.execute('INSERT INTO result_rows (file_id,event_id,person_id,program,year,date,section,position,class,car_number,driver,vehicle,color,total,raw_time,pax_time,total_s,raw_s,pax_s,cells,line_no,raw_line,class_place,laps,best_lap,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (i, ev, pid, m['program'], year, m['date'] or None, r['section'], r.get('position'), r.get('class'), r.get('car_number'), d,
             r.get('vehicle'), r.get('color'), r.get('total'), r.get('raw_time'), r.get('pax_time'), num(r.get('total')), num(r.get('raw_time')), num(r.get('pax_time')),
             json.dumps(r['cells']), r['line_no'], r['raw'], r.get('class_place'), r.get('laps'), r.get('best_lap'), r.get('status')))
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
