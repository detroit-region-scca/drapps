"""Member Milestones (service awards and new members) rules, free of Flask and Google so they can be tested on their own.

The awards live in a Google Sheet (tab "Awards": Month | Name | Years | Note), one row per member per month,
so anyone can fix a name by hand. drscca.org reads the same sheet and lays it out.
"""
import re
from datetime import date

MILESTONES = (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70)   # 0 = joined that month (new member)
COLS = ['Month', 'Name', 'Years', 'Note']


def valid_month(m):
    if not re.fullmatch(r'\d{4}-\d{2}', m or ''):
        return False
    return 1 <= int(m[5:]) <= 12


def this_month(today=None):
    t = today or date.today()
    return f'{t.year:04d}-{t.month:02d}'


def next_month(today=None):
    t = today or date.today()
    return f'{t.year + (t.month == 12):04d}-{t.month % 12 + 1:02d}'


def rows_from_form(month, by_years):
    """{years: 'one name per line'} -> [[month, name, years, '']], skipping blank lines, de-duplicated per month."""
    out, seen = [], set()
    for years in sorted(by_years, reverse=True):
        for line in (by_years[years] or '').splitlines():
            name = re.sub(r'\s+', ' ', line).strip(' ,;\t')
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append([month, name, int(years), ''])
    return out


def by_month(rows):
    """[[Month, Name, Years, Note], ...] -> [(month, {years: [names]})] newest first; malformed rows skipped."""
    months = {}
    for r in rows:
        r = list(r) + [''] * (4 - len(r))
        m, n = str(r[0]).strip(), str(r[1]).strip()
        try:
            y = int(str(r[2]).strip())
        except ValueError:
            continue
        if valid_month(m) and n and y >= 0:
            months.setdefault(m, {}).setdefault(y, []).append(n)
    return [(m, dict(sorted(g.items(), reverse=True))) for m, g in sorted(months.items(), reverse=True)]


def replace_month(rows, month, new_rows):
    """All rows with this month's rows swapped for new_rows, newest month first (the sheet's own order)."""
    keep = [list(r) for r in rows if str((list(r) + [''])[0]).strip() != month]
    out = keep + [list(r) for r in new_rows]
    return sorted(out, key=lambda r: str(r[0]), reverse=True)


# ---------- SCCA "Member List" report (my.scca.com > Region Resources > Region Reporting) ----------

def _date(v):
    if hasattr(v, 'year') and hasattr(v, 'month'):
        return date(v.year, v.month, getattr(v, 'day', 1))
    s = str(v or '').strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            from datetime import datetime
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def roster_members(rows):
    """[(name, region join date)] from the report's rows (first row = headings). Only the name and the Region Join
    Date are kept; the report's addresses, emails and birth dates are never returned. Raises ValueError if the
    file is not that report."""
    rows = [r for r in rows if r and any(c not in (None, '') for c in r)]
    if not rows:
        raise ValueError('The file is empty.')
    head = [str(h or '').strip().lower() for h in rows[0]]

    def col(name):
        return head.index(name) if name in head else None
    first, last, suffix, joined = col('first name'), col('last name'), col('suffix'), col('region join date')
    if None in (first, last, joined):
        raise ValueError('That is not the SCCA Member List report (no First Name, Last Name and Region Join Date columns).')
    out = []
    for r in rows[1:]:
        r = list(r) + [None] * len(head)
        name = ' '.join(str(r[i]).strip() for i in (first, last, suffix) if i is not None and r[i] not in (None, ''))
        d = _date(r[joined])
        if name and d:
            out.append((name, d))
    return out


def milestones_for(members, month):
    """{years: [names]} for a month: years 0 = joined the region that month, else each 5-year anniversary."""
    y, mo = int(month[:4]), int(month[5:])
    out = {}
    for name, d in members:
        years = y - d.year
        if d.month == mo and years >= 0 and years % 5 == 0:
            out.setdefault(years, []).append(name)
    return {k: sorted(v, key=lambda n: (n.split()[-1].lower() if n.split() else '', n.lower())) for k, v in sorted(out.items(), reverse=True)}


def reminder(today, app_url='https://drapps.drscca.org'):
    """(subject, body) of the monthly Member Milestones reminder, sent on the 1st; None on other days."""
    if today.day != 1:
        return None
    month = f'{today.year:04d}-{today.month:02d}'
    label = today.strftime('%B %Y')
    return (f'Member Milestones for {label}',
            f"""It's time to post {label}'s new members and membership anniversaries.

1. In my.scca.com, go to Region Resources > Region Reporting, choose Report Type "Member List" and click Download:
   https://my.scca.com/eweb/DynamicPage.aspx?webcode=regionreports
2. Open Member Milestones and upload that file (drapps keeps the original in a private Drive folder):
   {app_url}/sa/?month={month}
3. Check the names and click Publish to drscca.org. The site shows them within the hour.

Then delete the downloaded copy from your computer; it has members' addresses and birth dates.""")


def roster_expiry(rows):
    """{SCCA member ID: membership good-through date} from the report's rows: the later of SCCA Member Expire and
    Region Member Expire. Only the ID and that date are kept."""
    rows = [r for r in rows if r and any(c not in (None, '') for c in r)]
    if not rows:
        return {}
    head = [str(h or '').strip().lower() for h in rows[0]]
    idc = head.index('member id') if 'member id' in head else None
    cols = [i for i, h in enumerate(head) if h in ('scca member expire', 'region member expire')]
    out = {}
    if idc is None:
        return out
    for r in rows[1:]:
        r = list(r) + [None] * len(head)
        mid = str(r[idc] or '').strip()
        if mid.endswith('.0'):
            mid = mid[:-2]
        dates = [d for d in (_date(r[i]) for i in cols) if d]
        if mid and dates:
            out[mid] = max(dates)
    return out


def read_roster_full(data):
    """Bytes of the SCCA Member List .xlsx -> (members [(name, region join date)], expiry {member ID: date})."""
    import io
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ValueError('Could not open that file. Download the Member List again from SCCA (an .xlsx file) and retry.')
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    return roster_members(rows), roster_expiry(rows)


def read_roster(data):
    """Bytes of the SCCA Member List .xlsx -> [(name, region join date)]. ValueError if it is not that report."""
    import io
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ValueError('Could not open that file. Download the Member List again from SCCA (an .xlsx file) and retry.')
    return roster_members(list(wb.worksheets[0].iter_rows(values_only=True)))


def same_names(a, b):
    """True when two {years: [names]} hold the same people in the same groups (order ignored)."""
    norm = lambda g: {int(k): sorted(n.lower() for n in v) for k, v in g.items() if v}
    return norm(a) == norm(b)
