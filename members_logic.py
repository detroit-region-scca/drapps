"""Members: worker points and member milestones in one place. Rules only (no Flask, no Google), so they test alone.

The private "DRSCCA Members" sheet has two tabs:
  Members  Member # | First | Last | Life | Member since | Membership expires | On SCCA list of | Points (formula) |
           Lapsed since | Rejoined (kept up to date by each SCCA list load)
  Points   Date | Member # | Name | Points | What for | Event | Kind | Status | Entered by | Reconciled (when it was booked in QuickBooks)

A point is worth $5. Only Approved entries count. Nothing is added or spent until the keeper (or an officer) clicks:
event chairs' entries arrive as Pending and count once the keeper approves them.
"""
import re
from collections import defaultdict
from datetime import date, datetime

POINT_VALUE = 5
MEMBER_COLS = ['Member #', 'First', 'Last', 'Life', 'Member since', 'Membership expires', 'On SCCA list of', 'Points',
               'Lapsed since', 'Rejoined']
POINT_COLS = ['Date', 'Member #', 'Name', 'Points', 'What for', 'Event', 'Kind', 'Status', 'Entered by', 'Reconciled']
KINDS = ('Opening', 'Earned', 'Role points', 'Used', 'Forfeit', 'Adjustment')
APPROVED, PENDING = 'Approved', 'Pending'

# Point structure per day (from the worker point form)
POINT_STRUCTURE = [('Event chairman (one per event)', 4), ('Special events', 3),
                   ('Chief (one per specialty) / assistant chair (one per event)', 2), ('Worker', 1)]

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


# ---------- the entry grid ----------

SPECIALTIES = ['Event chair / assistant chair', 'Registration', 'Tech / scrutineering', 'Timing and scoring', 'Flagging and communications',
               'Grid / pit out', 'Course / course workers', 'Safety / emergency', 'Special event', 'Other']
VOLUNTEER_JOB = 'Volunteer job (role points)'


def rows_from_form(names, points, members_by_id, event_code, when, specialty, default_points=None):
    # specialty: what the points are for (the event, or a volunteer job)
    """Steve's worker point form: a specialty for the sheet, then Worker's Name | Points pairs. Names picked from the
    list carry the member #; typed names are kept for the keeper to match. Blank points use default_points (1 a day)."""
    out, problems = [], []
    kind = 'Role points' if specialty == VOLUNTEER_JOB else 'Earned'
    for raw, pts_raw in zip(names, points):
        raw = (raw or '').strip()
        if not raw:
            continue
        mid = raw.split(' ')[0]
        m = members_by_id.get(mid)
        pts = num(pts_raw) if str(pts_raw or '').strip() else (default_points or 0)
        if pts <= 0:
            problems.append(f'{raw}: points must be more than zero (to spend points, switch to Use points)'); continue
        out.append({'date': when, 'member': mid if m else '', 'name': full_name(m) if m else raw, 'points': pts,
                    'what': specialty or 'Worker', 'event': event_code or '', 'kind': kind})
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


def lapse_status(m, hit, list_date):
    """(lapsed since, rejoined) after a list load. Current = on the list and not expired on the list date. A member who
    stops being current gets "lapsed since" (their expiry date, or the list date if they left the list); a lapsed member
    who is current again gets "rejoined" = the list date. Life members never lapse."""
    lapsed, rejoined = m.get('lapsed'), m.get('rejoined')
    current = m.get('life') or (hit is not None and (not hit.get('expires') or hit['expires'] >= list_date))
    if current and lapsed:
        return None, list_date
    if not current and not lapsed:
        return ((hit or {}).get('expires') or m.get('expires') or list_date), rejoined
    return lapsed, rejoined


def merge_roster(members, report, list_date):
    """Members rows updated from the report: names and dates refreshed, new members added, everyone on it marked with
    the list date, and lapsed/rejoined kept up to date. Members not on it keep their row (and points). SCCA numbers
    (500100_0, 500200_1) match the ledger's (500100, 500200-1); a family number falls back to its base number."""
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
        lapsed, rejoined = lapse_status(m, hit, list_date)
        out.append(dict(m, lapsed=lapsed, rejoined=rejoined))
    have = {canon(m['member']) for m in out}
    for mid, hit in report.items():
        c = canon(mid)
        if c not in seen and c not in have:
            out.append({'member': c, 'first': hit['first'], 'last': hit['last'], 'life': False, 'joined': hit['joined'],
                        'expires': hit['expires'], 'listed': list_date, 'points': 0.0, 'lapsed': None, 'rejoined': None})
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


def books(entries, events_by_code):
    """Journal entry lines for these entries: points earned (by program), role points and adjustments are an expense to
    the program and an increase in the reserve; forfeits release reserve to Operations. Points used are paid by their
    own check or card charge (listed, no journal line)."""
    earned = defaultdict(float)
    forfeits = 0.0
    used = []
    for e in entries:
        if e['status'] != APPROVED:
            continue
        if e['kind'] in ('Earned', 'Adjustment') and e['points'] > 0:
            earned[program_for(e['event'], events_by_code)] += e['points']
        elif e['kind'] == 'Adjustment':           # a negative adjustment releases reserve like a forfeit
            forfeits += e['points']
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
                      'amount': round(-forfeits * POINT_VALUE, 2), 'what': 'points forfeited or written off'})
    return {'lines': lines, 'used': used}


def month_books(entries, events_by_code, year, month):
    return books([e for e in entries if isinstance(e['date'], date) and (e['date'].year, e['date'].month) == (year, month)], events_by_code)


def unreconciled(entries):
    """Approved entries not yet marked as booked in QuickBooks (Opening balances never need booking)."""
    return [e for e in entries if e['status'] == APPROVED and not e.get('reconciled') and e['kind'] != 'Opening']

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


def books_email(bk, label, reserve=None, value=None):
    """(subject, body) of the journal entry email. bk = {'jes': journal_entries(...)[0], 'used': [...]}."""
    lines = [f'Worker points journal entries to book: {label}.', '']
    for n, je in enumerate(bk['jes'], start=1):
        lines += [f"Journal entry {n}: {je['title']}", f"  Memo:   {je['memo']}",
                  f"  Debit   {je['debit']}   ${je['amount']:,.2f}", f"  Credit  {je['credit']}   ${je['amount']:,.2f}", '']
    if not bk['jes']:
        lines += ['Nothing to book: no points were earned or forfeited.', '']
    if bk['used']:
        lines.append('Points used (each paid by its own check or card charge; no journal entry):')
        lines += [f"  {e['date']}: {e['name']}, {e['what']}, {-e['points']:g} points (${-e['points'] * POINT_VALUE:,.2f})" for e in bk['used']]
        lines.append('')
    if reserve is not None and value is not None:
        diff = round(value - reserve, 2)
        lines.append('Worker points tie to the QuickBooks reserve.' if abs(diff) < 0.005 else
                     f'Worker points are ${diff:,.2f} off the QuickBooks reserve (${reserve:,.2f}) until these are booked.')
    lines += ['', 'After booking, mark them reconciled in DRSCCA Apps > Members > QuickBooks.']
    return f'Worker points journal entries to book ({label})', '\n'.join(lines)


# ---------- Auto add: people who get points every month (prepared as Pending; the keeper approves) ----------

AUTO_COLS = ['Member #', 'Name', 'Role', 'Points a month (Apr-Oct)', 'Points a month (Nov-Mar)']


def auto_points(row, month):
    return num(row['season'] if 4 <= month <= 10 else row['offseason'])


def auto_entries(auto_rows, entries, year, month):
    """The month's auto-add entries not yet prepared (pending or approved), dated the 1st."""
    first = date(year, month, 1)
    label = first.strftime('%B %Y')
    done = {(e['member'], e['what']) for e in entries}
    out = []
    for r in auto_rows:
        pts = auto_points(r, month)
        what = f"Role points, {label}: {r['role']}"
        if pts > 0 and r['member'] and (r['member'], what) not in done:
            out.append({'date': first, 'member': r['member'], 'name': r['name'], 'points': pts, 'what': what, 'event': '', 'kind': 'Role points'})
    return out


def journal_entries(entries, events_by_code):
    """One journal entry per event (points earned there), one for role points and other points with no event, and one
    for forfeits and write-offs. Each: {'title', 'memo', 'debit', 'credit', 'amount', 'points', 'entries'}.
    Points used are listed separately: each is paid by its own check or card charge."""
    groups, used = {}, []
    for e in entries:
        if e['status'] != APPROVED:
            continue
        if e['kind'] == 'Used':
            used.append(e)
            continue
        if e['kind'] == 'Forfeit' or (e['kind'] == 'Adjustment' and e['points'] < 0):
            key = ('~forfeit', '')
        elif e['event'] and e['kind'] in ('Earned', 'Adjustment'):
            key = ('event', e['event'])
        else:
            key = ('~role', '')
        groups.setdefault(key, []).append(e)
    out = []
    for (kind, code), es in sorted(groups.items(), key=lambda kv: kv[0]):
        pts = round(sum(e['points'] for e in es), 3)
        if abs(pts) < 0.004:
            continue
        if kind == 'event':
            ev = events_by_code.get(code.split('-')[0]) or {}
            prog = program_for(code, events_by_code)
            title = f"{code} {ev.get('event_name', '')}".strip()
            debit, credit = PROGRAM_ACCOUNT.get(prog, PROGRAM_ACCOUNT['OPS']), RESERVE
            memo = f"Worker points earned at {title} ({pts:g} points)"
        elif kind == '~role':
            title, debit, credit = 'Role points and other points', PROGRAM_ACCOUNT['OPS'], RESERVE
            memo = f"Worker points for volunteer jobs and adjustments ({pts:g} points)"
        else:
            title, debit, credit = 'Forfeits and write-offs', RESERVE, PROGRAM_ACCOUNT['OPS']
            memo = f"Worker points forfeited or written off ({-pts:g} points)"
        amount = round(abs(pts) * POINT_VALUE, 2)
        if kind != '~forfeit' and pts < 0:      # a net negative event or role group reverses the entry
            debit, credit = credit, debit
        dates = [e['date'] for e in es if e['date']]
        out.append({'title': title, 'memo': memo, 'debit': debit, 'credit': credit, 'amount': amount, 'points': pts,
                    'first': min(dates) if dates else None, 'last': max(dates) if dates else None, 'entries': es})
    return out, used
