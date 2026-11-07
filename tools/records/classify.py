import json, os, re, sys, csv, collections, datetime
S = sys.argv[1]
idx = json.load(open(f'{S}/index.json')); T = json.load(open(f'{S}/text.json'))
MON = {m: i for i, m in enumerate(['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'], 1)}
def ok(y, m, d):
    return 1980 <= y <= 2026 and 1 <= m <= 12 and 1 <= d <= 31
def y4(y):
    y = int(y); return y + (2000 if y < 50 else 1900) if y < 100 else y
def dates(s):
    out = []
    for m in re.finditer(r'(?<!\d)((?:19|20)\d\d)[-_](\d{1,2})[-_](\d{1,2})(?!\d)', s):
        out.append((int(m[1]), int(m[2]), int(m[3])))
    for m in re.finditer(r'(?<!\d)((?:19|20)\d\d) ?[-_.]? ?(\d\d) ?[-_.]? ?(\d\d)(?!\d)', s):          # 2022-11-05, 20200927
        out.append((int(m[1]), int(m[2]), int(m[3])))
    for m in re.finditer(r'(?<!\d)(\d{1,2})[-/_.](\d{1,2})[-/_.]((?:19|20)?\d\d)(?!\d)', s):     # 7-15-2017, 5/21/16
        out.append((y4(m[3]), int(m[1]), int(m[2])))
    for m in re.finditer(r'(?<!\d)(\d\d)(\d\d)((?:19|20)\d\d)(?!\d)', s):                       # 06292015
        out.append((int(m[3]), int(m[1]), int(m[2])))
    for m in re.finditer(r'(?<!\d)(\d\d)(\d\d)(\d\d)(?!\d)', s):                                # 103111
        out.append((y4(m[3]), int(m[1]), int(m[2])))
    for m in re.finditer(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*[-&]\s*\d{1,2})?,?\s+((?:19|20)\d\d)', s, re.I):
        out.append((int(m[3]), MON[m[1].lower()[:3]], int(m[2])))
    return [d for d in out if ok(*d)]
def year_of(s):
    m = re.findall(r'(?<!\d)((?:19|20)\d\d)(?!\d)', s)
    m = [int(x) for x in m if 1980 <= int(x) <= 2026]
    return m[0] if m else None
def program(n, t):
    nn = re.sub(r'[_\-.]+', ' ', n.lower())
    s = nn + ' ' + t[:600].lower()
    if 'workerpoints' in s.replace(' ', '') or 'worker points' in s or 'points used after' in s: return 'Worker points'
    if 'sae international' in s or re.search(r'fsae (mi )?\d{4} result|\d{4} fsae result', nn): return 'Other'
    if re.search(r'hill ?climb|quick guide|guide v\d|kart ?2 ?kart|live timing test|test of live uploaded', s): return 'Other'
    if re.search(r'rally ?-?cross|rally ?-?x\b|rallyx', nn + ' ' + t[:300].lower()): return 'RallyCross'   # named as RallyCross, even if AXWare timed it
    if re.search(r'axware|saginaw valley|total registered|active heat|raw time|\bpax', s): return 'Autocross'
    if re.search(r'rally ?cross|rally ?x\b|rallyx|\brx|\br x\b|run 1 pen|first name last name yr make|car # det driver|run group|thayerauto', s): return 'RallyCross'
    if re.search(r'\brr championship|son of sno|sno\*? ?drift|dawnbuster|twilight ?tour|clare color|\bcct|pavement ends|\bpe \d{4}|\bsos \d{4}|\btt \d{4}|\bmm ?\d{4}|moonlight|navigator|co-driver|\bleg 1|rolly|\bpor\b|press on regardless|\btsd\b|rally', s): return 'Rally'
    if re.search(r'axware|\bsolo|\bpax|autocross|raw time|test (and|&|n) tune|final results|total registered|active heat|season opener|season ender|memorial|summer heat|fall into|oscoda|glss|gass|cendiv|gldiv|divisional|cones|\bindex|ssp bsp|\bssp\b|saginaw valley|drscca points|season points|drive into spring|before nationals', s): return 'Autocross'
    if re.search(r'\brace|racing|qualif|\blap|sprints?\b|grand prix|enduro|shiloff|sprints|race\d\d|results grp|driver.?s school|gingerman|grattan|waterford|mid-ohio|nelson ledges|\bmis\b|majors|runoffs', s): return 'Road Racing'
    return None
rows = []
for f in idx:
    if re.search(r'-\d{2,4}x\d{2,4}\.(jpg|jpeg|png)$', f['name'], re.I): continue   # WordPress resized copies
    n, t = f['name'], T.get(f['id'], '')
    prog = program(n, t)

    lay = os.path.expanduser(f"~/scca-data/text/{f['id']}.txt")
    if os.path.exists(lay): tt = open(lay, errors='ignore').read()[:4000]
    elif f['name'].lower().endswith(('.htm', '.html')):    # web pages: drop the style block, keep the AXWare header
        h = open(f"{S}/files/{f['id']}", errors='ignore').read()
        tt = re.sub(r'<[^>]+>', ' ', re.sub(r'(?is)<(style|script)\b.*?</\1\s*>', ' ', h))[:4000]
    else: tt = t[:3000]
    hm = re.search(r'^(.*?)\b(?:mon|tue|wed|thu|fri|sat|sun)\w*\s+(\d\d)-(\d\d)-((?:19|20)\d\d)', tt, re.I | re.M)
    hd = None
    if hm and ok(int(hm[4]), int(hm[2]), int(hm[3])): hd = (int(hm[4]), int(hm[2]), int(hm[3]))
    ed = (dates(hm[1]) or [None])[0] if hm else None        # a date written in the event name, e.g. "#10 - 20171008 Season Ender"
    nd = (dates(n) or [None])[0]
    weekend = hd and datetime.date(*hd).weekday() >= 5
    # The header stamp can be the setup or export day: event-name date first, then the file name, then the stamp.
    d = ed or (hd if weekend and (not nd or nd[0] != hd[0]) else (nd or hd or (dates(t[:800]) or [None])[0]))
    yr = d[0] if d else (year_of(n) or year_of(t[:400]) or (int(f['path']) if f['path'].isdigit() else None))
    if d and year_of(n) and d[0] != year_of(n) and len(dates(n)) == 0: d = None   # text date contradicts the name's year
    kind = 'points' if prog not in (None, 'Worker points') and (re.search(r'points|standings|rr.championship', n.lower()) or re.search(r'season points|points standings|championship standings|official points|overall scores|usrrc|road rally challenge', (n + ' ' + t[:300]).lower())) else 'event'
    rows.append(dict(id=f['id'], name=n, oldpath=f['path'], program=prog or 'Not sorted', year=yr or '', date='%04d-%02d-%02d' % d if d else '', kind=kind, text=t[:160]))
csv.DictWriter(open(f'{S}/sorted.csv', 'w', newline=''), fieldnames=list(rows[0])).writerows([dict(zip(rows[0], rows[0]))] + rows)
c = collections.Counter(r['program'] for r in rows); print(c)
print('no year:', sum(1 for r in rows if not r['year']), ' no date:', sum(1 for r in rows if not r['date'] and r['program'] != 'Not sorted'))
print('year differs from old folder:', sum(1 for r in rows if r['year'] and r['oldpath'].isdigit() and int(r['oldpath']) != r['year']))
