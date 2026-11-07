"""AXWare (the region's autocross and RallyCross timing software) results sheets, read strictly.

Three kinds of sheet, each recognized by its title line:
- Class results ("Final Results", "Class Results"): per class, the place (1T = trophy), class, car #, driver, car,
  each run (time, +n cones at 2 s each, +dnf/+dsq/+off) and the total. Some layouts print the class as a heading
  ("'A Stock' - Total Entries: 2" or "as - 'A Stock' - Total Entries: 2"), newer ones in a column. Two-day events print a
  D1 and a D2 line per driver, with the two-day total on the D1 line. Mobile-format pages may print no total: it is the
  best run with penalties, as AXWare computes it.
- PAX ("Pax Time Results", "Pax Final Results"): PAX place, sometimes the class place, class, car #, driver, car, raw
  time, *factor, PAX time.
- Raw ("Raw Time Results"): row (overall raw place), class place, class, car #, driver, car, raw time.

read(text) returns (rows, kind, expected) where expected is the count the sheet prints ("with Times: 154",
"Timed Entries: 157"), so the caller can refuse a sheet that was not read completely.
"""
import re

TIME = r'\d{1,3}\.\d{3}'
RUN = re.compile(r'(?<![\d.])(' + TIME + r')(\+(?:\d+|dnf|dsq|off|rrn|dns))?(?![\d.])', re.I)
FACTOR = re.compile(r'\*\s?(\d?\.\d{3,4})')
PLACE = r'(\d{1,3}T?m?|DNF|DNS|DQ)'
CLS = r'[A-Za-z][A-Za-z0-9-]{0,9}'


def kind_of(text):
    head = '\n'.join(text.splitlines()[:8]).lower()
    if 'pax' in head or 'index' in head:
        return 'pax'
    if 'raw time' in head:
        return 'raw'
    if 'final results' in head or 'class results' in head or 'total entries' in text.lower():
        return 'class'
    return None


def expected(text, kind):
    if kind == 'class':
        m = re.search(r'with Times:\s*(\d+)', text)
    else:
        m = re.search(r'Timed Entries:\s*(\d+)', text) or re.search(r'with Times:\s*(\d+)', text)
    return int(m[1]) if m else None


def flip(n):
    """'Frisch, Biff' -> 'Biff Frisch' (suffixes kept: 'Roberts II, Keith' -> 'Keith Roberts II')."""
    if ',' in n:
        last, first = [x.strip() for x in n.split(',', 1)]
        if first and not re.fullmatch(r'(?i)jr\.?|sr\.?|ii|iii|iv', first):
            return f'{first} {last}'
    return n


def split_name(rest):
    """'Pat Example         2018 Mazda Miata   Red ...' -> ('Pat Example', '2018 Mazda Miata')."""
    m = re.match(r"\s*([A-Za-z][A-Za-z'.\- ]*?[A-Za-z.)])(?:\s{2,}|\s+(?=(?:19|20)?\d{2}\s+[A-Z]))(.*)$", rest)
    if not m:
        return flip(rest.strip()), ''
    car = re.split(r'\s{2,}', m[2].strip())[0] if m[2].strip() and not RUN.match(m[2].strip()) else ''
    return flip(' '.join(m[1].split())), car


def best(runs):
    vals = []
    for t, pen in runs:
        p = (pen or '').lower().lstrip('+')
        if p in ('dnf', 'dsq', 'off', 'rrn', 'dns'):
            continue
        vals.append(round(float(t) + 2 * (int(p) if p.isdigit() else 0), 3))
    return min(vals) if vals else None


def read_class(text):
    rows, section, sect_name = [], '', ''
    lines = text.splitlines()
    for n, line in enumerate(lines, 1):
        st = line.strip()
        h = re.match(r"^(?:([a-z0-9-]{1,8})\s+-\s+)?'([^']+)'\s*-\s*Total Entries", st, re.I)
        if h:
            section, sect_name = (h[1] or h[2]), h[2]
            continue
        h2 = re.match(r"^([a-z0-9-]{1,8})\s+-\s+'([^']+)'\s*$", st, re.I)
        if h2:
            section, sect_name = h2[1], h2[2]
            continue
        m = re.match(r'^\s*' + PLACE + r'\s+(?:(' + CLS + r')\s+)?(\d{1,4})\s+(.*)$', line)
        if not m or re.match(r'\s*D2\b', line):
            continue
        place, cls, num, rest = m[1], m[2] or section, m[3], m[4]
        runs = [(r[1], r[2]) for r in RUN.finditer(rest) if float(r[1]) >= 15]      # under 15 s is a Diff column, never a run
        if not re.search(r'[A-Za-z]{2}', rest[:40]):
            continue
        name, car = split_name(re.sub(r'\s{2,}(?:D1|' + TIME + r').*$', '', rest))
        two_day = re.search(r'\bD1\b', rest)
        if not two_day:                     # runs wrapped onto the next line(s) (times only; never the member # some print there)
            k = n
            while k < len(lines) and not re.match(r'^\s*' + PLACE + r'\s+', lines[k]) and not re.search(r"Total Entries|^\s*'", lines[k]) \
                    and RUN.search(lines[k]) and not re.search(r'[A-Za-z]{3}', lines[k]):
                more = [(r[1], r[2]) for r in RUN.finditer(lines[k]) if float(r[1]) >= 15]
                runs = runs[:-1] + more + runs[-1:] if runs and not runs[-1][1] else runs + more
                k += 1
        day2 = []
        if two_day and n < len(lines) and re.match(r'^\s*D2\b', lines[n].strip()):
            day2 = [(r[1], r[2]) for r in RUN.finditer(lines[n])]
        total = None
        if two_day:
            # D1 line: runs then the two-day total as the last value
            if runs and not runs[-1][1]:
                total = runs[-1][0]
                runs = runs[:-1]
        else:
            b = best(runs)
            last = runs[-1][0] if runs and not runs[-1][1] else None
            if last is not None and b is not None and abs(float(last) - b) < 0.0015 and len(runs) >= 2:
                total, runs = last, runs[:-1]           # the printed Total column
                b = best(runs) if runs else b
            elif last is not None and b is not None and len(runs) >= 2 and abs(float(last) - best(runs[:-1] or runs)) < 0.0015:
                total, runs = last, runs[:-1]
            else:
                total = f'{b:.3f}' if b is not None else None   # no total printed: AXWare's best run with penalties
        if not runs and total is None:
            status = 'no time'
        else:
            status = None
        rows.append({'position': place, 'class': (cls or '').lower(), 'section': sect_name or (cls or ''), 'car_number': num,
                     'driver': name, 'vehicle': car or None, 'total': total, 'status': status,
                     'cells': [['runs', ' '.join(t + (p or '') for t, p in runs + day2)]], 'line_no': n, 'raw': st})
    return rows


def read_pax(text):
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        f = FACTOR.search(line)
        if not f:
            continue
        pre, post = line[:f.start()], line[f.end():]
        m = re.match(r'^\s*(\d{1,3})\s+(?:(\d{1,3}T?m?)\s+)?(' + CLS + r')\s+(\d{1,4})\s+(?:R\s+)?(.*?)\s+(' + TIME + r')\S*\s*$', pre)
        if not m:
            continue
        name, car = split_name(m[5])
        pax = re.match(r'\s*(' + TIME + ')', post)
        raw = m[6]
        if pax and abs(float(raw) - float(pax[1])) < 0.0005 and abs(float(f[1]) - 1) > 0.0005:
            raw = None              # pro / novice rows print the PAX time in the Total column: it is not a raw time
        rows.append({'position': m[1], 'class_place': m[2], 'class': m[3].lower(), 'section': '', 'car_number': m[4], 'driver': name,
                     'vehicle': car or None, 'raw_time': raw, 'pax_time': pax[1] if pax else None, 'total': None,
                     'cells': [['factor', f[1]]], 'line_no': n, 'raw': line.strip()})
    return rows


def read_raw(text):
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        m = re.match(r'^\s*(\d{1,3})\s+(\d{1,3}T?m?)\s+(' + CLS + r')\s+(\d{1,4})\s+(.*)$', line)
        if not m:
            continue
        t = RUN.search(m[5])
        if not t:
            continue
        name, car = split_name(m[5][:t.start()])
        rows.append({'position': m[1], 'class_place': m[2], 'class': m[3].lower(), 'section': '', 'car_number': m[4], 'driver': name,
                     'vehicle': car or None, 'raw_time': t[1], 'total': None, 'cells': [], 'line_no': n, 'raw': line.strip()})
    return rows


def read(text):
    k = kind_of(text)
    if not k:
        return [], None, None
    rows = {'class': read_class, 'pax': read_pax, 'raw': read_raw}[k](text)
    return rows, k, expected(text, k)


def dedupe(rows):
    """A sheet printed twice (some PDFs repeat every page): each driver/time once."""
    seen, out = set(), []
    for r in rows:
        k = (r['driver'].lower(), r.get('class'), r.get('total'), r.get('raw_time'), r.get('pax_time'), r.get('position'))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def _num(v):
    try:
        return float(re.match(r'\d+\.\d+', v or '')[0])
    except (TypeError, ValueError):
        return None


def check(rows, kind, expected):
    """(ok, note): the sheet was read completely and consistently. With a printed count, the timed drivers read must
    match it; without one, places must agree with times (within each class for class results, overall for PAX/raw)."""
    timed = [r for r in rows if r.get('total') or r.get('raw_time') or r.get('pax_time')]
    if not rows:
        return False, 'nothing read'
    if expected:
        if not (0.97 * expected <= len(timed) <= expected * 1.03 + 1):
            return False, f'{len(timed)} of {expected} timed drivers read'
    key = 'total' if kind == 'class' else 'pax_time' if kind == 'pax' else 'raw_time'
    groups = {}
    for r in timed:
        groups.setdefault((r.get('section') or r.get('class')) if kind == 'class' else '', []).append(r)
    bad = 0
    for g in groups.values():
        vals = [_num(r.get(key)) for r in g]
        vals = [v for v in vals if v is not None]
        bad += sum(1 for a, b in zip(vals, vals[1:]) if b + 0.0005 < a)
    if bad > max(2, 0.03 * len(timed)):
        return False, f'times out of order in {bad} places'
    return True, f'{len(timed)} timed drivers' + (f' of {expected}' if expected else '')
