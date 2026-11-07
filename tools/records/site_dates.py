"""Event dates from the old website's posts and pages (announcements, results posts, calendars), for events whose files
give no date. A date counts when it sits near the event's name and falls in the event's year. Usage:
site_dates.py <scca.db> <site api dir> [--apply]"""
import collections, datetime, glob, html, json, re, sqlite3, sys
db = sqlite3.connect(sys.argv[1]); api = sys.argv[2]; apply = '--apply' in sys.argv
MON = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}
DATE = re.compile(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*[-&]\s*\d{1,2})?(?:,?\s+((?:19|20)\d\d))?\b', re.I)
DMY = re.compile(r'\b(\d{1,2})-(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*-(\d{2}|\d{4})\b', re.I)   # "14-Oct-23"
def all_dates(text, py):
    """(position, date, has_year) for every date written in the text; dates without a year take the post's year."""
    out = []
    for d in DATE.finditer(text):
        y = int(d[3]) if d[3] else py
        try: out.append((d.start(), datetime.date(y, MON[d[1].lower()[:3]], int(d[2])), bool(d[3])))
        except (ValueError, TypeError): pass
    for d in DMY.finditer(text):
        y = int(d[3]) + (2000 if len(d[3]) == 2 else 0)
        try: out.append((d.start(), datetime.date(y, MON[d[2].lower()[:3]], int(d[1])), True))
        except ValueError: pass
    return sorted(out, key=lambda x: x[0])
docs, raw, by_id = [], [], {}
for f in glob.glob(f'{api}/pages-*.json') + glob.glob(f'{api}/posts-*.json'):
    for p in json.load(open(f)):
        h = p.get('content', {}).get('rendered', '')
        t = html.unescape(re.sub(r'<[^>]+>', ' ', h))
        doc = (p.get('date', '')[:10], html.unescape(p['title']['rendered']) + ' . ' + ' '.join(t.split()))
        docs.append(doc); raw.append((doc[0], h)); by_id[p['id']] = doc
media = {}           # file name on the old site -> the post it was attached to
for f in glob.glob(f'{api}/media-*.json'):
    for m in json.load(open(f)):
        media[m.get('source_url', '').rsplit('/', 1)[-1].lower()] = m.get('post')
def link_texts(fname):
    """The table row, list item or paragraph holding each link to this file on the old site."""
    out = []
    for pd, h in raw:
        for m in re.finditer(re.escape(fname), h, re.I):
            lo = max(h.rfind('<tr', 0, m.start()), h.rfind('<li', 0, m.start()), h.rfind('<p', 0, m.start()))
            ends = [x for x in (h.find('</tr>', m.end()), h.find('</li>', m.end()), h.find('</p>', m.end())) if x != -1]
            hi = min(ends) if ends else m.end() + 200
            seg = html.unescape(re.sub(r'<[^>]+>', ' ', h[max(lo, m.start() - 1500): hi]))
            out.append((pd, ' '.join(seg.split())))
    return out
def dates_in(text, year, py, weight, votes):
    for pos, dt, has_year in all_dates(text, py):
        if dt.year != year or dt.weekday() in (1, 2, 3): continue    # events run Friday to Monday; midweek is a posting date
        near_day = re.search(r'(?i)(sun|mon|tues|wednes|thurs|fri|satur)day,?\s*$', text[:pos])
        votes[dt.isoformat()] += weight * ((2 if has_year else 1) + (2 if near_day else 0))
def keys(name):
    """Words to look for: the event's name without 'Rally', and its well-known short forms."""
    n = re.sub(r'(?i)\b(rally|solo|results?|final|resultspost\w*|\d+)\b', ' ', name)
    n = ' '.join(n.split()).replace('*', '').strip()
    k = [n] if len(n) >= 5 else []
    if re.search(r'(?i)press on regardless|\bpor\b', n): k += ['Press On Regardless', 'POR']
    if re.search(r'(?i)sno', n): k += ['Sno*Drift', 'SnoDrift', 'Sno Drift', 'Son of Sno']
    if re.search(r'(?i)^mm$|moonlight', n): k += ['Moonlight Monte']
    return k
found = {}
for eid, prog, year, name in db.execute('select id, program, year, name from events where date is null and year is not null').fetchall():
    votes = collections.Counter()
    for pd, text in docs:
        py = int(pd[:4]) if pd[:4].isdigit() else None
        for k in keys(name):
            rx = r'\b' + re.escape(k).replace(r'\ ', r'\s*').replace(r'\*', r'\*?') + r'\b'
            for m in re.finditer(rx, text, 0 if k.isupper() else re.I):      # "POR" as a word, not inside "report"
                ctx = text[max(0, m.start() - 250): m.end() + 250]
                dates_in(ctx, year, py, 1, votes)
    for (orig,) in db.execute('select name from files where event_id = ?', (eid,)):
        base = re.sub(r'^\d{4}-\d{2}-\d{2} ', '', orig).lower()
        for pd, seg in link_texts(base):                         # pages linking to the results file: the row's date
            ds = [x for x in all_dates(seg, int(pd[:4]) if pd[:4].isdigit() else None) if x[1].year == year and x[1].weekday() not in (1, 2, 3)]
            if len({x[1] for x in ds}) == 1: votes[ds[0][1].isoformat()] += 8      # one date in the row: that is it
        post = media.get(base)
        if post and post in by_id:                               # the post the file was attached to
            pd, txt = by_id[post]
            dates_in(txt[:3000], year, int(pd[:4]) if pd[:4].isdigit() else None, 1, votes)
    if votes:
        top = votes.most_common(2)
        best, n = top[0]; second = top[1][1] if len(top) > 1 else 0
        total = sum(votes.values())
        if n >= 3 and n >= 2 * second:         # clearly the most mentioned date
            found[eid] = best
            print(f'{prog:10} {year} {name[:40]:40} -> {best}  ({n}/{total})')
print(len(found), 'dates found on the old website')
if '--why' in sys.argv:
    pass
if apply:
    for eid, d in found.items():
        db.execute('update events set date = ? where id = ? and date is null', (d, eid))
        db.execute('update files set date = coalesce(date, ?) where event_id = ?', (d, eid))
        db.execute('update result_rows set date = coalesce(date, ?) where event_id = ?', (d, eid))
    db.commit()
