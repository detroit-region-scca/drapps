"""Add season_standings to scca.db from the final season points sheet of each program and year."""
import json, os, re, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
NAME = re.compile(r"(?<![A-Za-z])([A-Z][A-Za-z'\-.]+(?: (?:[A-Z][A-Za-z'\-.]*|de|da|van|von|del|la|le)){1,4}(?:,? (?:Jr|Sr|II|III|IV)\.?)?)(?: \([a-z]\))?")
NUM = re.compile(r'(?<![\w.])-?\d{1,4}(?:\.\d{1,2})?(?![\w.])')
def pick(files):
    def score(f):
        n = f[1].lower()
        s = 0
        if re.search(r'final|year ?end|trophies|yearend|december', n): s += 100
        if 'prelim' in n: s -= 50
        if re.search(r'gld|great.lakes|championship.tour|thayer', n): s -= 500
        if re.search(r'drscca|detroit|dr_scca|det_', n): s += 20
        m = re.search(r'(\d+) ?of ?(\d+)|event ?(\d+)|after ?(\d+)', n)
        if m: s += int(next(x for x in m.groups() if x))
        m = re.match(r'^(\d{10})_', n)
        if m: s += int(m[1]) / 1e9          # unix upload time prefix: later is newer
        return (s, f[2] or '', f[3])
    return max(files, key=score)
RALLY = r'Equipped|Limited|Stock|Novice|Expert|Master|Unlimited|Historic|SOP|Open|Unequipped|Beginner|Intermediate'
db.executescript('''DROP TABLE IF EXISTS season_standings;
CREATE TABLE season_standings (id INTEGER PRIMARY KEY, program TEXT, year INTEGER, class TEXT, place INTEGER, person_id INTEGER REFERENCES people(id),
  name TEXT, score REAL, file_id INTEGER REFERENCES files(id), line_no INTEGER, raw_line TEXT);''')
people = {n.lower(): i for i, n in db.execute('select id, name from people')}
groups = {}
RALLY_CLASSES = r'Equipped|GPS|Limited|Stock|Novice|Unlimited|SOP|Open|Sport|Tour'
def rally_template(lines, target=None):
    """The Detroit Region road rally "Class Standings" sheet (2018 on): Class, Organizer (rallies the person organized,
    "SoSD,FPOR,SPOR"), Place, Name, DR member (DET / YES), then places and points per rally, Best last. Returns
    [(class, name, score, line_no, line)]: the member column and organizer codes never end up in names."""
    out, cls = [], ''
    for n, l in enumerate(lines, 1):
        st = l.strip()
        c = re.match(r'^(%s)\b' % RALLY_CLASSES, st)
        if c:
            cls, st = c[1], st[c.end():].strip()
        mem = re.search(r'(?<=[a-z.])\s*\b(DET|YES|NO|WMR|SBR|NEO|\*)\b(?=\s|$)', st)
        if not mem:
            continue
        name = st[:mem.start()]
        name = re.sub(r'^(?:(?:[A-Z][A-Za-z]{0,4})(?:\s*,\s*|\s+))*?(?=\d+\s+[A-Z][a-z]|[A-Z][a-z]+\s+[A-Z])', '', name)  # organizer codes
        name = ' '.join(re.sub(r'^\d+\s+', '', name).split())                                                   # place
        if not re.fullmatch(r"[A-Z][A-Za-z'.\-]+(?: [A-Z][A-Za-z'.\-]+){1,3}", name) or not cls:
            continue
        at = l.find(st[mem.end():]) if st[mem.end():] else len(l)
        nums = [(x.start() + at, x.end() + at, float(x[1])) for x in re.finditer(r'(?<![\w.])(\d+(?:\.\d+)?)(?![\w])', st[mem.end():])]
        if not nums:
            continue
        sc = min(nums, key=lambda x: abs((x[0] + x[1]) / 2 - target))[2] if target is not None else nums[-1][2]   # the Best column
        out.append((cls, name, sc, n, l.strip()))
    return out


for f in db.execute("select id, name, date, length(text), program, year, text from files where kind='points' and program in ('Autocross','RallyCross','Rally','Road Racing') and year is not null and length(text) > 200"):
    groups.setdefault((f[4], f[5]), []).append(f)
total = 0
CURATED = os.environ.get('CURATED_DIR', '')
def curated_standings(prog, year):
    """A season transcribed from its final standings sheet (CURATED_DIR/standings/<Program>-<year>.json): the places
    as the sheet prints them (ties, members-only eligibility and trophy order included), never re-ranked here."""
    p = os.path.join(CURATED, 'standings', f'{prog}-{year}.json') if CURATED else ''
    return json.load(open(p)) if p and os.path.exists(p) else None
done = set()
for prog in ('Autocross', 'RallyCross', 'Rally', 'Road Racing'):
    for year in range(1980, 2031):
        cur = curated_standings(prog, year)
        if not cur:
            continue
        fid = next((r[0] for r in db.execute('select id from files where original_drive_url like ?', (f"%{cur.get('gid', '-')}%",))), None)
        for cl in cur.get('classes', []):
            for n, r in enumerate(cl.get('rows', []), 1):
                for who in [x.strip() for x in [r.get('name') or ''] + (r.get('partner') or '').split(' & ') if x.strip()]:
                    pid = people.get(who.lower())
                    if pid is None:
                        pid = db.execute('insert into people (name) values (?)', (who,)).lastrowid; people[who.lower()] = pid
                    sc = r.get('score')
                    db.execute('insert into season_standings (program,year,class,place,person_id,name,score,file_id,line_no,raw_line) values (?,?,?,?,?,?,?,?,?,?)',
                               (prog, year, cl.get('class') or None, int(r['place']), pid, who, float(sc) if sc not in (None, '') else None, fid, n, 'transcribed'))
                    total += 1
        done.add((prog, year))
for (prog, year), files in sorted(groups.items()):
    if (prog, year) in done:
        continue
    f = pick(files); fid, text = f[0], f[6]
    text = text.replace('\u2010', '-').replace('\u2011', '-').replace('\u2013', '-').replace('\u2014', '-')
    lines = text.splitlines()
    best = tot = cls_col = None; start = 0
    for k, l in enumerate(lines[:40]):
        if re.search(r'\b(total|best)\b', l, re.I) and start == 0: start = k + 1
        if best is None and re.search(r'\bbest\b', l, re.I):
            m = re.search(r'\bbest[^ ]*(?: (?:of )?\d+(?: of \d+)?| (?:three|four|five|six))?', l, re.I); best = (m.start() + m.end()) / 2
        if tot is None and re.search(r'\btotal\b', l, re.I):
            m = re.search(r'\btotal\b', l, re.I); tot = (m.start() + m.end()) / 2
        if cls_col is None and re.search(r'\bclass\b', l, re.I) and re.search(r'name|driver|number|region|position|car', l, re.I):
            cls_col = re.search(r'\bclass\b', l, re.I).start()
    target = best if best is not None else tot
    cls, out = '', []
    if prog == 'Rally' and re.search(r'Organizer', text) and re.search(r'(?i)\bmember\b', text):
        out = rally_template(lines, target)
        if out:
            lines = []                    # read by its own reader: skip the general one below
    for l in lines[:start]:                       # a class named in the page title, above the column header
        t = re.search(r'(?i)points\s*-\s*([A-Z]{1,5}L?)\s*$', l.strip())
        if t: cls = t[1].upper()
    sheet = f[1].lower().endswith(('.xls', '.xlsx'))
    sheet_col = None
    if sheet:
        for l in lines[:60]:
            cs = [c.strip().lower() for c in l.split('   ')]
            if any(re.search(r'driver|name', c) for c in cs):
                hit = next((i for i, c in enumerate(cs) if re.search(r'\bbest\b|^total|\bpoints\b', c)), None)
                if hit is not None: sheet_col = hit; break
    for n, l in enumerate(lines, 1):
        if n <= start and not sheet: continue
        m2 = re.search(r"(?<![A-Za-z])([A-Z][a-z][A-Za-z'.\-]*)\s{2,}([A-Z][A-Za-z'.\-]+(?:,? (?:Jr|Sr|II|III)\.?)?)(?=\s{2,}[\d-])", l)
        m = NAME.search(l)
        if m2 and (not m or m2.start() <= m.start() or ' ' not in m[1].strip()):
            class _M:                       # behave like a regex match over the two columns
                def __init__(s, a): s.a = a
                def __getitem__(s, i): return f"{s.a[1]} {s.a[2]}" if i == 1 else s.a[0]
                def start(s): return s.a.start()
                def end(s): return s.a.end()
            m = _M(m2)
        nums = [(x.start(), x.end(), float(x.group())) for x in NUM.finditer(l)]
        after = [x for x in nums if m and x[0] > m.end()]
        if not m or len(after) < 1:
            st = l.strip()
            h1 = re.match(r"^([A-Z]{1,5}L?)\s+-\s+([A-Z][A-Za-z ]{2,30}?)(?:\s{2,}.*)?$", st)
            h2 = re.match(r'^([A-Z]{1,6}L?)\s+Drivers:', st)
            h3 = re.match(r'^([A-Z]{1,6}L?)$', st)
            h4 = re.match(r'^(%s)\b' % RALLY, st)
            h5 = re.match(r'^([A-Z][A-Za-z\- ]+?)\s*\(([A-Z0-9]{1,6}L?)\)\s*$', st)        # "A-Street Prepared (ASP)"
            h6 = re.search(r'(?i)points\s*-\s*([A-Z]{1,5}L?)\s*$', st)                    # "2006 Rallycross Points - SSF"
            if h5: cls = f"{h5[2]} - {h5[1].strip()}"; continue
            if h6: cls = h6[1].upper(); continue
            if h1: cls = f"{h1[1]} - {h1[2].strip()}"
            elif h2 or h3: cls = (h2 or h3)[1]
            elif h4 and prog == 'Rally': cls = h4[1]
            continue
        name = m[1].strip()
        if re.search(r'^(Event|Season|Total|Best|Denotes|Detroit|Drivers|Class|Road|Street|Page|Opener|Ender|Car|Region|Position|Trophy)\b', name) or re.search(r'\b(Number|Region|Drivers|Points|Rally|Championship|National|Division|Series|Standings|Tour)\b', name): continue
        before = l[:m.start()].strip()
        if cls_col is not None and prog != 'Rally':
            cells = [(x.start(), x.group()) for x in re.finditer(r'\S+', l[:m.start()])]
            c = [v for s0, v in cells if abs(s0 - cls_col) <= 6 and not v.isdigit()]
            c = [v for v in c if v not in ('T', '?', 'Y', 'N') and not re.match(r'^\d', v)]
            if c and c[0] != 'Class': cls = c[0]
        elif prog == 'Rally' and re.match(r'^(%s)\b' % RALLY, before):
            cls = re.match(r'^(%s)\b' % RALLY, before)[1]
        after = [x for x in after if x[2] >= 0 and not (x[2] == int(x[2]) and 1900 <= x[2] <= 2100)]
        if not after: continue
        cells = l.split('   ')
        if sheet and sheet_col is not None and sheet_col < len(cells) and re.fullmatch(r'-?\d+(?:\.\d+)?', cells[sheet_col].strip()):
            sc = float(cells[sheet_col])          # spreadsheet: the value in the column titled Best / Total / Points
        elif target is not None:
            sc = min(after, key=lambda x: abs((x[0] + x[1]) / 2 - target))[2]
        else:
            sc = max(x[2] for x in after[:3])
        out.append((cls, name, sc, n, l.strip()))
    if len(out) >= 10 and not any(r[0] for r in out):
        out = [('Overall',) + r[1:] for r in out]         # one table for the whole series, no classes
    if len(out) < 10 or sum(1 for r in out if r[0]) < 0.7 * len(out) or (len({r[0] for r in out}) < 2 and out[0][0] != 'Overall'):
        print('skipped', prog, year, f[1]); continue
    named = any(re.match(r'^[A-Z0-9]{1,6}L? - ', r[0] or '') for r in out)
    if named:   # proper "XX - Name" classes: drop the overall / lowercase tables printed below them
        out = [r for r in out if re.match(r'^[A-Z0-9]{1,6}L? - ', r[0] or '')]
    out = [r for r in out if not re.match(r'^\(?T\)?$', r[0] or '')]
    by, seen = {}, set()
    for r in out:
        if (r[0], r[1].lower()) in seen: continue
        seen.add((r[0], r[1].lower())); by.setdefault(r[0], []).append(r)
    for c, rs in by.items():
        rs.sort(key=lambda r: -r[2]); place = 0; prev = None
        for k, r in enumerate(rs, 1):
            if r[2] != prev: place = k; prev = r[2]
            pid = people.get(r[1].lower())
            if pid is None:
                pid = db.execute('insert into people (name) values (?)', (r[1],)).lastrowid; people[r[1].lower()] = pid
            db.execute('insert into season_standings (program,year,class,place,person_id,name,score,file_id,line_no,raw_line) values (?,?,?,?,?,?,?,?,?,?)',
                       (prog, year, c or None, place, pid, r[1], r[2], fid, r[3], r[4]))
            total += 1
db.execute('create index ss_person on season_standings(person_id)')
db.commit()
print(total, 'standings rows from', len(groups), 'seasons')
