"""Members: worker points and member milestones in one place. Rules only (no Flask, no Google), so they test alone.

The private "DRSCCA Members" sheet has two tabs:
  Members  Member # | First | Last | Life | Member since | Membership expires | On SCCA list of | Points (formula) |
           Lapsed since | Rejoined (kept up to date by each SCCA list load)
  Points   Date | Member # | Name | Points | What for | Event | Kind | Status | Entered by | Reconciled (when it was booked in QuickBooks)

A point is worth $5. Only Approved entries count; entries are saved Approved (officials pick names from the member
list), so they count at once.
"""
import re
from collections import defaultdict
from datetime import date, datetime
from utils import to_number

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
        return to_number(str(v).replace(',', '').replace('$', ''))
    except (TypeError, ValueError):
        return 0.0


def nice(n):
    """The ledger mixes "PAT EXAMPLE" with "McSample": only fix the all-capitals ones."""
    n = (n or '').strip()
    return n.title() if n.isupper() else n


def full_name(m):
    return f"{nice(m.get('first'))} {nice(m.get('last'))}".strip()


MAX_POINTS_PER_LINE = 50   # a typo guard: a 3-day event chair earns 12; a year's role points stay under 50


# ---------- the worker points sheet (upload) and the review step ----------

NAME_HEAD = re.compile(r"(?i)^\s*(workers?'?s?\s+)?name\s*$")
POINTS_HEAD = re.compile(r'(?i)^\s*points?\s*$')
SUFFIX = re.compile(r'(?i)[ ,]+(jr|sr|ii|iii|iv)\.?$')


def _cell(v):
    return '' if v is None else str(v).strip()


TOTAL = re.compile(r'(?i)^\s*total\b')


def read_points_sheet(sheets):
    """The worker point form as volunteer leads send it: a few title lines, then Worker's Name | Points pairs (often two
    pairs side by side), then a Total line and a footer (who submitted it, where to mail it). sheets: [(tab, rows)].
    Every tab with the Name / Points heading and at least one line is read (a Day 1 and a Day 2 tab). A tab's list ends
    at its Total line, or at two empty rows in a row, so the footer is never read as names; anything left in the name
    columns below that is counted in 'below' for the page to mention. A name with no number in Points keeps points None.
    Returns {'tabs': [{'tab', 'title', 'lines', 'total', 'below'}], 'lines': every line} or raises ValueError."""
    tabs = []
    for tab, rows in sheets:
        rows = [list(r) for r in rows]
        for hi, r in enumerate(rows):
            cells = [_cell(c) for c in r]
            pairs = []
            for i, c in enumerate(cells):
                if NAME_HEAD.match(c):
                    j = next((k for k in range(i + 1, len(cells)) if cells[k]), None)
                    if j is not None and POINTS_HEAD.match(cells[j]):
                        pairs.append((i, j))
            if not pairs:
                continue
            width = max(j for _, j in pairs) + 1
            title = [t for t in (_cell(x) for row in rows[:hi] for x in row) if t][:6]
            lines, total, started, blanks, end, done = [], None, False, 0, len(rows), set()
            for k in range(hi + 1, len(rows)):
                row = rows[k] + [None] * (width - len(rows[k]))
                got = [(_cell(row[i]), row[j]) for i, j in pairs]
                live = [(q, n, p) for q, (n, p) in enumerate(got) if q not in done]   # a column ends at its own Total
                if all(not n and _cell(p) == '' for _, n, p in live):
                    blanks += 1
                    if started and blanks >= 2:
                        end = k
                        break
                    continue
                blanks = 0
                hit = any(TOTAL.match(n) for _, n, _ in live)
                for q, n, p in live:
                    if TOTAL.match(n):
                        total = (total or 0) + num(p)
                        done.add(q)
                        continue
                    if (hit or total is not None) and not n and _cell(p) == '':
                        done.add(q)       # after a Total, a column ends at its first empty cell (the footer follows)
                        continue
                    started = True
                    if not n:
                        if _cell(p):
                            lines.append({'name': '', 'points': None, 'raw': _cell(p), 'cell': f'row {k + 1}', 'tab': tab})
                        continue
                    pv = p if isinstance(p, (int, float)) and not isinstance(p, bool) else (num(p) if re.fullmatch(r'\s*\d+(\.\d+)?\s*', _cell(p)) else None)
                    lines.append({'name': ' '.join(n.split()), 'points': float(pv) if pv is not None else None, 'raw': _cell(p),
                                  'cell': f'row {k + 1}', 'tab': tab})
                if len(done) == len(pairs):
                    end = k
                    break
            if total is None:                  # the Total a row or two below where the list stopped
                for kk in range(end, min(end + 4, len(rows))):
                    rr = rows[kk] + [None] * (width - len(rows[kk]))
                    hits = [num(rr[j]) for i, j in pairs if TOTAL.match(_cell(rr[i]))]
                    if hits:
                        total, end = sum(hits), kk
                        break
            # after a Total line comes the footer (submitted by, mail to); without one, names further down may be missed
            # (only a name with a number beside it counts: the old form's footer has text but no points)
            below = 0 if total is not None else sum(1 for rr in rows[end + 1:] for i, j in pairs
                                                    if j < len(rr) and _cell(rr[i]) and isinstance(rr[j], (int, float)) and not isinstance(rr[j], bool))
            if lines:
                tabs.append({'tab': tab, 'title': title, 'lines': lines, 'total': total, 'below': below})
            break
    if not tabs:
        raise ValueError("No Worker's Name / Points list in that file. Use the worker points form (Worker's Name and Points columns).")
    return {'tabs': tabs, 'lines': [x for t in tabs for x in t['lines']]}


def _plain(n):
    n = ' '.join(str(n or '').replace('.', ' ').split()).strip()
    if ',' in n:                                     # "Last, First"
        last, first = [x.strip() for x in n.split(',', 1)]
        if first and not SUFFIX.fullmatch(',' + first):
            n = f'{first} {last}'
    return SUFFIX.sub('', n).lower()


def candidates(name, members, limit=8):
    """Members a sheet name might be: the same last name (suffixes and "Last, First" ignored), the same first initial
    first. Shown in a short dropdown; never picked for the user."""
    t = _plain(name).split()
    if not t:
        return []
    first, last = t[0], t[-1]
    out = []
    for m in members:
        mt = _plain(full_name(m)).split()
        if mt and mt[-1] == last:
            out.append((0 if mt[0][:1] == first[:1] else 1, full_name(m).lower(), m))
    return [m for _, _, m in sorted(out, key=lambda x: (x[0], x[1]))][:limit]


def review_lines(lines, members, entries, event_code):
    """Each submitted line ready for the review page: the member it is (only an exact pick or an exact full name that
    fits one member), the members it might be, the points it already has for this event, and what is wrong with it.
    lines: [{'name', 'points'}]."""
    by_id = {m['member']: m for m in members}
    have = existing_points(entries, event_code)
    seen, out = {}, []
    for n, ln in enumerate(lines):
        mid = pick_id(ln['name'], by_id)
        cands = candidates(ln['name'], members) if not mid else []
        if not mid and not cands:
            same = [m for m in members if full_name(m).lower() == ' '.join(ln['name'].split()).lower()]
            cands = same[:8]                          # the same name twice on the list: pick which
        pts = ln.get('points')
        problem = ''
        if pts is None:
            problem = 'No points on the sheet'
        elif pts <= 0 or pts > MAX_POINTS_PER_LINE:
            problem = f'{pts:g} points: check it'
        dup = have.get(canon(mid)) if mid else None
        twice = mid and canon(mid) in seen
        if mid:
            seen.setdefault(canon(mid), n)
        out.append({'n': n, 'name': ln['name'], 'points': pts, 'member': mid, 'member_name': full_name(by_id[mid]) if mid else '',
                    'candidates': [{'member': m['member'], 'label': pick_label(m), 'already': have.get(canon(m['member']))} for m in cands],
                    'problem': problem,
                    'already': dup, 'twice': bool(twice)})
    return out


def existing_points(entries, event_code):
    """{member: {'points', 'count', 'by'}} already saved for this event (Approved, earned). event_code: the event's code,
    or a set of its codes (an event can have a custom tag and its unique code). No event, no check."""
    out = {}
    codes = {c for c in ([event_code] if isinstance(event_code, str) else event_code or []) if c}
    if not codes:
        return out
    for e in entries:
        if e.get('event') in codes and e.get('status') == APPROVED and (e.get('points') or 0) > 0 and e.get('member'):
            d = out.setdefault(canon(e['member']), {'points': 0.0, 'count': 0, 'by': []})
            d['points'] += e['points']; d['count'] += 1
            if e.get('by') and e['by'] not in d['by']:
                d['by'].append(e['by'])
    return out


def save_check(members_by_id, entries, event_code, chosen, allow_duplicates=False):
    # event_code: the event's code or the set of its codes
    """The last check before saving, run again on the server whatever the page showed. chosen: [{'member', 'points',
    'seen'}] (seen = how many entries the member already had for this event when the review page was made). Returns
    problems: an unknown member, bad points, a member twice, or points saved for the event since the review."""
    have = existing_points(entries, event_code)
    problems, count = [], defaultdict(int)
    for c in chosen:
        m = members_by_id.get(c['member'])
        if not m:
            problems.append(f"{c.get('name') or c['member']}: not on the member list"); continue
        if not (0 < c['points'] <= MAX_POINTS_PER_LINE):
            problems.append(f"{full_name(m)}: {c['points']:g} points; use more than 0 and at most {MAX_POINTS_PER_LINE}")
        count[canon(c['member'])] += 1
        if count[canon(c['member'])] == 2:
            problems.append(f"{full_name(m)} is listed twice: leave one out or add the points together")
        h = have.get(canon(c['member']))
        if h and not allow_duplicates and h['count'] > (c.get('seen') or 0):
            problems.append(f"{full_name(m)} already has {h['points']:g} points for this event (entered by {', '.join(h['by']) or 'someone'})")
    return problems


# ---------- balances, lapsed ----------

def balances(entries):
    out = defaultdict(float)
    for e in entries:
        if e['status'] == APPROVED and e['member']:
            out[e['member']] += e['points']
    return {k: round(v, 3) for k, v in out.items()}


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


def pick_label(m):
    """How a member shows in the name pickers: name first, member # in brackets."""
    return f"{full_name(m)} ({m['member']})"


def pick_id(text, members_by_id):
    """Member # from a picker entry: 'First Last (500100)', an older '500100 First Last', or a name typed out in full
    that matches exactly one member. '' if none matches."""
    t = ' '.join(str(text or '').split())
    if not t:
        return ''
    hit = re.search(r'\(([^()]+)\)$', t)
    if hit and hit.group(1).strip() in members_by_id:
        return hit.group(1).strip()
    if t.split(' ')[0] in members_by_id:
        return t.split(' ')[0]
    same = [mid for mid, m in members_by_id.items() if full_name(m).lower() == t.lower()]
    return same[0] if len(same) == 1 else ''


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


# ---------- QuickBooks ----------

def program_for(code, events_by_code):
    code = (code or '').strip()
    base = code.split('-')[0] if code.startswith('#') else code
    ev = events_by_code.get(base)
    return ev['program_code'] if ev else 'OPS'


def entry_key(e):
    """What one Points entry is, whatever row it sits on: its date, member, points and what for. The QuickBooks page
    sends it with each ticked row, so a row that moved in the sheet is not marked by mistake."""
    d = e.get('date')
    return '|'.join([d.isoformat() if d else '', str(e.get('member', '')), f"{e.get('points') or 0:g}", str(e.get('what', '')), str(e.get('event', ''))])


def unreconciled(entries):
    """Approved entries not yet marked as booked in QuickBooks (Opening balances never need booking)."""
    return [e for e in entries if e['status'] == APPROVED and not e.get('reconciled') and e['kind'] != 'Opening']

USE_FOR = ['SCCA membership renewal', 'Event entry', 'Awards banquet', 'Region merchandise', 'Other']


def use_entry(member, points, what, when):
    """One Used entry, or (None, problem)."""
    try:
        p = abs(to_number(points))
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


# ---------- Auto add: people who get points every month (added on the 1st) ----------

AUTO_COLS = ['Member #', 'Name', 'Role', 'Points a month (Apr-Oct)', 'Points a month (Nov-Mar)']


def auto_points(row, month):
    return num(row['season'] if 4 <= month <= 10 else row['offseason'])


def auto_entries(auto_rows, entries, year, month):
    """The month's auto-add entries not yet added, dated the 1st."""
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
