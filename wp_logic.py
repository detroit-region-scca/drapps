"""Worker points rules, kept free of Flask and Google so they can be tested on their own.

A point is worth $5. The ledger keeps each member's opening position (from the keeper's 8/31/2026
file) and every change after it as a row on the Transactions tab: Earned and Allocation rows are
positive, Used and Forfeit rows are negative.
"""
from collections import defaultdict
from datetime import date

POINT_VALUE = 5
TYPES = ('Earned', 'Allocation', 'Used', 'Forfeit', 'Adjustment')

# Which QBO worker points expense account a program's earned points are booked to.
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


def base_code(code):
    """'#DR9H6-FRI' -> '#DR9H6' (day suffixes share the event's program)."""
    code = (code or '').strip()
    return code.split('-')[0] if code.startswith('#') else code


def program_for(code, events_by_code):
    """Program of a transaction's event code; allocations and anything unknown fall to Operations."""
    ev = events_by_code.get(base_code(code))
    return ev['program_code'] if ev else 'OPS'


def allocation_points(role_row, month):
    """Points a role earns for a calendar month (1-12): its Apr-Oct rate or its Nov-Mar rate."""
    in_season = 4 <= month <= 10
    return float(role_row['season'] if in_season else role_row['offseason'])


def membership_lapsed(exp, today):
    """exp is the keeper's 'M/YY' (membership good through the end of that month)."""
    try:
        m, y = exp.split('/')
        m, y = int(m), 2000 + int(y)
    except (ValueError, AttributeError):
        return False
    return (y, m) < (today.year, today.month)


def forfeit_candidates(members, today):
    """Members with points left whose membership expired before this month. Life members never lapse."""
    out = []
    for m in members:
        if m.get('life') or m['balance'] <= 0.004:
            continue
        if membership_lapsed(m.get('exp', ''), today):
            out.append(m)
    return sorted(out, key=lambda m: (m.get('exp_sort', ''), m['last']))


def month_end_summary(transactions, events_by_code, year, month):
    """What the month's ledger activity means for QBO.

    Earned and allocated points are an expense to the program and an increase in the reserve.
    Forfeits release reserve back to Operations Worker Points. Used points (renewals, banquet) are
    paid out with their own check or card charge against the reserve, so they need no journal entry
    here; they are listed so the treasurer can tick them off against those payments.
    """
    earned = defaultdict(float)
    forfeits = used = adjustments = 0.0
    used_rows = []
    for t in transactions:
        d = t['date']
        if not isinstance(d, date) or (d.year, d.month) != (year, month):
            continue
        pts = float(t['points'])
        if t['type'] in ('Earned', 'Allocation'):
            prog = 'OPS' if t['type'] == 'Allocation' else program_for(t['event_code'], events_by_code)
            earned[prog] += pts
        elif t['type'] == 'Forfeit':
            forfeits += pts
        elif t['type'] == 'Used':
            used += pts
            used_rows.append(t)
        elif t['type'] == 'Adjustment':
            adjustments += pts
    lines = []
    for prog in sorted(earned):
        if abs(earned[prog]) > 0.004:
            lines.append({'debit': PROGRAM_ACCOUNT.get(prog, PROGRAM_ACCOUNT['OPS']), 'credit': RESERVE,
                          'points': earned[prog], 'amount': round(earned[prog] * POINT_VALUE, 2), 'what': f'{prog} points earned'})
    if abs(forfeits) > 0.004:
        lines.append({'debit': RESERVE, 'credit': PROGRAM_ACCOUNT['OPS'], 'points': -forfeits,
                      'amount': round(-forfeits * POINT_VALUE, 2), 'what': 'Points forfeited (memberships lapsed or moved)'})
    return {'lines': lines, 'earned_points': sum(earned.values()), 'forfeit_points': forfeits,
            'used_points': used, 'used_rows': used_rows, 'adjust_points': adjustments,
            'net_change': round((sum(earned.values()) + forfeits + used + adjustments) * POINT_VALUE, 2)}


def validate_entry(member_id, points, typ, balances):
    """Return an error message, or None if the transaction may be posted."""
    if typ not in TYPES:
        return f'Unknown type {typ}'
    if member_id not in balances:
        return f'Member {member_id} is not in the ledger'
    if points == 0:
        return 'Points cannot be zero'
    if typ in ('Earned', 'Allocation') and points < 0:
        return f'{typ} points must be positive'
    if typ in ('Used', 'Forfeit') and points > 0:
        return f'{typ} points must be negative'
    if typ == 'Used' and balances[member_id] + points < -0.004:
        return f'Member has only {balances[member_id]:.1f} points'
    return None


# ---------- the "Report event workers" form on drscca.org (emailed to the points keeper, pasted in here) ----------

ROLE_POINTS = (('event chair', 4), ('chairman', 4), ('special', 3), ('assistant chair', 2), ('asst', 2), ('chief', 2), ('chair', 4))


def role_points(role):
    """Points per day for a role as typed on the form: event chair 4, special events 3, chief or assistant chair 2, else 1."""
    r = (role or '').lower()
    for key, pts in ROLE_POINTS:
        if key in r:
            return pts
    return 1


def parse_report(text, members):
    """Lines "Name, role, days" -> [{'line', 'member', 'label', 'duty', 'points', 'match'}]; 'match' is 'exact',
    'guess' (one member with that last name and first initial) or '' (nobody: the keeper picks). Days default to 1."""
    people = [(m['member'], f"{m['first']} {m['last']}".strip(), (m['first'] or '').strip().lower(), (m['last'] or '').strip().lower())
              for m in members]
    out = []
    for line in (text or '').splitlines():
        parts = [p.strip() for p in line.split(',')]
        if not parts or not parts[0] or parts[0].lower().startswith(('workers', 'name')):
            continue
        name, role = parts[0], (parts[1] if len(parts) > 1 else 'worker') or 'worker'
        try:
            days = float(parts[2]) if len(parts) > 2 and parts[2] else 1.0
        except ValueError:
            days = 1.0
        words = name.lower().replace('.', '').split()
        hit, how = None, ''
        exact = [p for p in people if f'{p[2]} {p[3]}' == ' '.join(words)]
        if len(exact) == 1:
            hit, how = exact[0], 'exact'
        elif words:
            guess = [p for p in people if p[3] == words[-1] and p[2][:1] == words[0][:1]]
            if len(guess) == 1:
                hit, how = guess[0], 'guess'
        out.append({'line': line.strip(), 'member': f'{hit[0]} {hit[1]}' if hit else '', 'label': name, 'duty': role.capitalize(),
                    'points': role_points(role) * days, 'match': how})
    return out
