"""Members: worker points and member milestones in one place. Rules only (no Flask, no Google), so they test alone.

The private "DRSCCA Members" sheet has two tabs:
  Members  Member # | First | Last | Life | Member since | Membership expires | On SCCA list of | Points (formula)
  Points   Date | Member # | Name | Points | What for | Event | Kind | Status | Entered by

A point is worth $5. Only Approved entries count. Nothing is added or spent until the keeper (or an officer) clicks:
event chairs' entries arrive as Pending and count once the keeper approves them.
"""
import re
from collections import defaultdict
from datetime import date, datetime

POINT_VALUE = 5
MEMBER_COLS = ['Member #', 'First', 'Last', 'Life', 'Member since', 'Membership expires', 'On SCCA list of', 'Points']
POINT_COLS = ['Date', 'Member #', 'Name', 'Points', 'What for', 'Event', 'Kind', 'Status', 'Entered by']
KINDS = ('Opening', 'Earned', 'Role points', 'Used', 'Forfeit', 'Adjustment')
APPROVED, PENDING = 'Approved', 'Pending'

# What a row on the entry page means: (label, points per day; None = type the points; negative = points used).
ROLES = [('Worker', 1), ('Chief or assistant chair', 2), ('Event chair', 4), ('Special event', 3),
         ('Role points (volunteer job)', None), ('Other', None)]

PROGRAM_ACCOUNT = {
    'AX': 'Club AX Autocross Expense:Club AX Autocross Worker Points',
    'RX': 'Club RX Rallycross Expense:Club RX Rallycross Worker Points',
    'RY': 'Club RY Road Rally Expense:Club RY Road Rally Worker Points',
    'RR': 'Club RR Road Racing Expense:Club RR Road Racing Worker Points',
    'DR': 'Club DR General Event Expense:Club DR General Event Worker Points',
    'SS': 'Club SS Street Survival Expense:Club SS Street Survival Worker Points',
    'SIM': 'Club DR General Event Expense:Club DR General Event Worker Points',
    'OPS': 'Operations Expense:Operations Worker Points',
}
RESERVE = 'Reserve Fund - Worker Awards'
MILESTONE_YEARS = (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70)   # 0 = joined that month


# ---------- small helpers ----------

def to_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and v > 20000:          # a Sheets serial date
        from datetime import timedelta
        return date(1899, 12, 30) + timedelta(days=int(v))
    s = str(v or '').strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def num(v):
    try:
        return float(str(v).replace(',', '').replace('$', ''))
    except (TypeError, ValueError):
        return 0.0


def nice(n):
    """The ledger mixes "ED ARTHUR" with "McClintock": only fix the all-capitals ones."""
    n = (n or '').strip()
    return n.title() if n.isupper() else n


def full_name(m):
    return f"{nice(m.get('first'))} {nice(m.get('last'))}".strip()


def kind_for(points, role):
    if points < 0:
        return 'Used'
    return 'Role points' if (role or '').startswith('Role points') else 'Earned'


def role_rate(role):
    return dict(ROLES).get(role)


# ---------- the entry grid ----------

def rows_from_grid(form_rows, members_by_id, event_code, when, what_default=''):
    """Entry-page rows -> (entries, problems). Each form row: member ('500100 Pat Quinn' or a typed name), role, days,
    points, note. Points left blank are worked out from the role and days. A row without a known member is a problem
    (the person must be added first), except that event chairs may send typed names for the keeper to sort out."""
    out, problems = [], []
    for r in form_rows:
        raw = (r.get('member') or '').strip()
        if not raw:
            continue
        mid = raw.split(' ')[0]
        m = members_by_id.get(mid)
        role = r.get('role') or 'Worker'
        try:
            days = float(r.get('days') or 1)
        except ValueError:
            days = 1.0
        pts_raw = (r.get('points') or '').strip()
        if pts_raw:
            pts = num(pts_raw)
        elif role_rate(role):
            pts = role_rate(role) * days
        else:
            problems.append(f'{raw}: enter the points'); continue
        if pts <= 0:
            problems.append(f'{raw}: points must be more than zero (use the Use points tab to spend points)'); continue
        note = (r.get('note') or '').strip()
        if role in ('Role points (volunteer job)', 'Other'):
            what = note or what_default or role
        else:
            what = f'{role}: {note}' if note else role
        out.append({'date': when, 'member': mid if m else '', 'name': full_name(m) if m else raw, 'points': pts,
                    'what': what, 'event': event_code or '', 'kind': kind_for(pts, role)})
    return out, problems


def check_spending(entries, balances):
    """Problems if any member would go below zero."""
    use = defaultdict(float)
    for e in entries:
        if e['points'] < 0 and e['member']:
            use[e['member']] += e['points']
    return [f"{mid}: has only {balances.get(mid, 0):g} points" for mid, p in use.items() if balances.get(mid, 0) + p < -0.004]


# ---------- balances, pending, lapsed ----------

def balances(entries):
    out = defaultdict(float)
    for e in entries:
        if e['status'] == APPROVED and e['member']:
            out[e['member']] += e['points']
    return {k: round(v, 3) for k, v in out.items()}


def pending_groups(entries):
    """Pending entries grouped as they were sent: [(key, {'event', 'date', 'by', 'rows'})], oldest first."""
    groups = {}
    for e in entries:
        if e['status'] != PENDING:
            continue
        key = f"{e['event']}|{e['date']}|{e['by']}"
        g = groups.setdefault(key, {'event': e['event'], 'date': e['date'], 'by': e['by'], 'rows': []})
        g['rows'].append(e)
    return sorted(groups.items(), key=lambda kv: str(kv[1]['date']))


def lapsed(members, today):
    """Members with points whose membership has lapsed per the latest SCCA list: not on it, or expired. Life members
    never lapse. Empty when no SCCA list has been loaded yet."""
    lists = [m['listed'] for m in members if m.get('listed')]
    if not lists:
        return []
    latest = max(lists)
    return sorted([m for m in members if not m.get('life') and m['points'] > 0.004
                   and (m.get('listed') != latest or (m.get('expires') and m['expires'] < today))],
                  key=lambda m: (m.get('expires') or date.min, m['last']))


# ---------- SCCA Member List report (my.scca.com > Region Resources > Region Reporting) ----------

def roster(rows):
    """Report rows (first = headings) -> {member #: {'first', 'last', 'joined', 'expires'}}. Only these are kept:
    the report's addresses, emails and birth dates are never read. ValueError if it is not that report."""
    rows = [r for r in rows if r and any(c not in (None, '') for c in r)]
    if not rows:
        raise ValueError('The file is empty.')
    head = [str(h or '').strip().lower() for h in rows[0]]

    def col(n):
        return head.index(n) if n in head else None
    idc, first, last, suffix, joined = col('member id'), col('first name'), col('last name'), col('suffix'), col('region join date')
    exp_cols = [i for i, h in enumerate(head) if h in ('scca member expire', 'region member expire')]
    if None in (idc, first, last, joined):
        raise ValueError('That is not the SCCA Member List report (no Member ID, names and Region Join Date).')
    out = {}
    for r in rows[1:]:
        r = list(r) + [None] * len(head)
        mid = re.sub(r'\.0$', '', str(r[idc] or '').strip())
        if not mid:
            continue
        exps = [d for d in (to_date(r[i]) for i in exp_cols) if d]
        last_name = ' '.join(str(r[i]).strip() for i in (last, suffix) if i is not None and r[i] not in (None, ''))
        out[mid] = {'first': str(r[first] or '').strip(), 'last': last_name, 'joined': to_date(r[joined]),
                    'expires': max(exps) if exps else None}
    return out


def canon(mid):
    """SCCA writes member numbers as 500100_0 (primary) and 500200_1 (family); the old ledger has 500100 and 500200-1.
    Both forms compare equal after this."""
    s = str(mid or '').strip().replace('-', '_')
    return re.sub(r'_0$', '', s)


def merge_roster(members, report, list_date):
    """Members rows updated from the report: names and dates refreshed, new members added, and everyone on it marked
    with the list date. Members not on it keep their row (and points) with the old list date. Family IDs like
    500200-1 take their dates from 500200 when the report lists only the base number."""
    by_canon = {canon(k): v for k, v in report.items()}
    out, seen = [], set()
    for m in members:
        c = canon(m['member'])
        hit = by_canon.get(c) or by_canon.get(c.split('_')[0])
        if hit:
            seen.add(c if c in by_canon else c.split('_')[0])
            m = dict(m, joined=hit['joined'] or m.get('joined'), expires=hit['expires'] or m.get('expires'), listed=list_date)
            if c in by_canon:
                m.update(first=hit['first'] or m['first'], last=hit['last'] or m['last'])
        out.append(m)
    have = {canon(m['member']) for m in out}
    for mid, hit in report.items():
        c = canon(mid)
        if c not in seen and c not in have:
            out.append({'member': c, 'first': hit['first'], 'last': hit['last'], 'life': False, 'joined': hit['joined'],
                        'expires': hit['expires'], 'listed': list_date, 'points': 0.0})
            have.add(c)
    return out

def milestones(members, month):
    """{years: [names]} for 'YYYY-MM' from members on the latest list: 0 = joined that month, else 5-year anniversaries."""
    y, mo = int(month[:4]), int(month[5:])
    lists = [m['listed'] for m in members if m.get('listed')]
    latest = max(lists) if lists else None
    out = defaultdict(list)
    for m in members:
        d = m.get('joined')
        if not d or (latest and m.get('listed') != latest):
            continue
        years = y - d.year
        if d.month == mo and years >= 0 and years % 5 == 0:
            out[years].append(full_name(m))
    return {k: sorted(v, key=lambda n: (n.split()[-1].lower(), n.lower())) for k, v in sorted(out.items(), reverse=True)}


def standings(members):
    """Public worker points list: names and points only, A-Z by last name."""
    rows = [{'name': full_name(m), 'points': round(m['points'], 1)} for m in members if m['points'] >= 0.05 and m['member'] != 'UNASSIGNED']
    return sorted(rows, key=lambda r: (r['name'].split()[-1].lower() if r['name'] else '', r['name'].lower()))


# ---------- QuickBooks ----------

def program_for(code, events_by_code):
    code = (code or '').strip()
    base = code.split('-')[0] if code.startswith('#') else code
    ev = events_by_code.get(base)
    return ev['program_code'] if ev else 'OPS'


def month_books(entries, events_by_code, year, month):
    """The month's journal entry lines: points earned (by program) and role points are an expense to the program and
    an increase in the reserve; forfeits release reserve to Operations. Points used are paid by their own check or
    card charge (listed, no journal line)."""
    earned = defaultdict(float)
    forfeits = 0.0
    used = []
    for e in entries:
        d = e['date']
        if e['status'] != APPROVED or not isinstance(d, date) or (d.year, d.month) != (year, month):
            continue
        if e['kind'] in ('Earned', 'Adjustment') and e['points'] > 0:
            earned[program_for(e['event'], events_by_code)] += e['points']
        elif e['kind'] == 'Role points':
            earned['OPS'] += e['points']
        elif e['kind'] == 'Forfeit':
            forfeits += e['points']
        elif e['kind'] == 'Used':
            used.append(e)
    lines = [{'debit': PROGRAM_ACCOUNT.get(p, PROGRAM_ACCOUNT['OPS']), 'credit': RESERVE, 'points': v,
              'amount': round(v * POINT_VALUE, 2), 'what': f'{p} points earned'} for p, v in sorted(earned.items()) if abs(v) > 0.004]
    if abs(forfeits) > 0.004:
        lines.append({'debit': RESERVE, 'credit': PROGRAM_ACCOUNT['OPS'], 'points': -forfeits,
                      'amount': round(-forfeits * POINT_VALUE, 2), 'what': 'points forfeited'})
    return {'lines': lines, 'used': used}


USE_FOR = ['SCCA membership renewal', 'Event entry', 'Awards banquet', 'Region merchandise', 'Other']


def use_entry(member, points, what, when):
    """One Used entry, or (None, problem)."""
    try:
        p = abs(float(points))
    except (TypeError, ValueError):
        p = 0
    if p <= 0:
        return None, 'Enter how many points to use.'
    if member['points'] + 1e-6 < p:
        return None, f"{full_name(member)} has only {member['points']:g} points."
    return {'date': when, 'member': member['member'], 'name': full_name(member), 'points': -p, 'what': what or 'Points used',
            'event': '', 'kind': 'Used'}, None
