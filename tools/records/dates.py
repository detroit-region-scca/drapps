"""Fill in event dates the file names did not give, from dates printed in the files' text (write-ups, score sheets,
OCR of scans). Only real dates in the event's own year are used. Usage: dates.py <scca.db>"""
import datetime, re, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
MON = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}
M = r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?'
PATS = [(re.compile(M + r'\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*[-&,]\s*\d{1,2})*,?\s+((?:19|20)\d\d)', re.I), lambda m: (int(m[3]), MON[m[1].lower()[:3]], int(m[2]))),
        (re.compile(r'(?<![\d/])(\d{1,2})/(\d{1,2})/((?:19|20)?\d\d)(?![\d/])'), lambda m: (int(m[3]) + (0 if len(m[3]) == 4 else 2000 if int(m[3]) < 50 else 1900), int(m[1]), int(m[2]))),
        (re.compile(r'(\d{1,2})\s+' + M + r'\s+((?:19|20)\d\d)', re.I), lambda m: (int(m[3]), MON[m[2].lower()[:3]], int(m[1]))),
        (re.compile(r'(?<!\d)(\d{1,2})' + M + r'(\d{2})(?!\d)', re.I), lambda m: (2000 + int(m[3]), MON[m[2].lower()[:3]], int(m[1])))]   # "24Sept05"
YEARLESS = re.compile(M + r'\s+(\d{1,2})(?:st|nd|rd|th)\b', re.I)          # "December 4th", when the season is known
def found(text, year):
    for rx, f in PATS:
        for m in rx.finditer(text[:6000]):
            try:
                y, mo, d = f(m); dt = datetime.date(y, mo, d)
            except (ValueError, KeyError):
                continue
            if dt.weekday() in (1, 2, 3):
                continue                     # midweek: a letter or posting date, not the event
            if year is None or y == year:
                return dt.isoformat()
    if year:
        m = re.search(r'(?<!\d)(\d{1,2})-(\d{1,2})\.\w{2,4}\b', text[:200])   # file name ending "-01-13.xlsx"
        if m:
            try:
                return datetime.date(year, int(m[1]), int(m[2])).isoformat()
            except ValueError:
                pass
        for m in YEARLESS.finditer(text[:3000]):
            try:
                dt = datetime.date(year, MON[m[1].lower()[:3]], int(m[2]))
            except (ValueError, KeyError):
                continue
            if dt.weekday() not in (1, 2, 3):
                return dt.isoformat()
    return None
n = 0
for eid, year in db.execute('select id, year from events where date is null').fetchall():
    for (text,) in db.execute("select name || ' ' || coalesce(text, '') from files where event_id = ?", (eid,)):
        d = found(text, year)
        if d:
            db.execute('update events set date = ?, year = coalesce(year, ?) where id = ?', (d, int(d[:4]), eid))
            db.execute('update files set date = coalesce(date, ?) where event_id = ?', (d, eid))
            db.execute('update result_rows set date = coalesce(date, ?), year = coalesce(year, ?) where event_id = ?', (d, int(d[:4]), eid))
            n += 1
            break
db.commit()
print(n, 'event dates found in the text;', db.execute('select count(*) from events where date is null').fetchone()[0], 'still undated')
