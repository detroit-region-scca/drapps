"""What the nightly QuickBooks sync (tools/qbo_nightly.py) concludes from the QuickBooks export. No I/O here.

QuickBooks accounts are named per program ("Club AX Autocross Revenue", "Club RX Rallycross Site Rental"),
so program actuals come straight from the region profit and loss. Everything that is not a "Club XX" account
(Operations, investment income) belongs to the Operations plan, program code OPS.
"""
import re
from datetime import date, timedelta

OPS = 'OPS'
MATCH_WINDOW_DAYS = 10      # a check can be dated a little before the request was filed (paid at the event)
AMOUNT_SLACK = 1.00         # requests are sometimes rounded on the check
WAITING_DAYS = 30
NO_ACTUALS_DAYS = 14


def program_of(account):
    m = re.match(r'Club (\w+) ', account or '')
    return m.group(1) if m else OPS


def program_totals(lines):
    """{program: {'income', 'expense', 'net', 'lines'}} from profit and loss account lines."""
    out = {}
    for ln in lines:
        p = out.setdefault(program_of(ln['account']), {'income': 0.0, 'expense': 0.0, 'lines': []})
        p['income' if ln['section'] == 'Income' else 'expense'] += ln['amount']
        p['lines'].append(ln)
    for p in out.values():
        p['income'], p['expense'] = round(p['income'], 2), round(p['expense'], 2)
        p['net'] = round(p['income'] - p['expense'], 2)
    return out


def check_number(paid_ref):
    m = re.search(r'check\s*#?\s*(\d{3,})', paid_ref or '', re.I)
    return m.group(1) if m else None


def _surnames(name):
    words = [w for w in re.findall(r'[a-z]+', (name or '').lower()) if len(w) >= 3]
    return set(words[-1:]) | ({words[0]} if len(words) == 1 else set())


def _same_person(a, b):
    """Last names agree ("Bob Jones" and "Robert Jones")."""
    sa, sb = _surnames(a), set(re.findall(r'[a-z]+', (b or '').lower()))
    return bool(sa and sa & sb)


def _day(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def is_card_statement(p):
    return p['kind'] == 'other' and (not p['payee'] or re.search(r'cardmember', p['payee'], re.I))


def match_funds(requests, payments):
    """Open requests that QuickBooks shows as paid: [(request, {'paid_date', 'paid_ref', 'paid_amount', 'note'})].

    A check matches on the payee's last name and the amount (within a dollar), dated no earlier than shortly
    before the request. A region card charge matches on the exact amount (with cents) on a card statement line.
    Only unambiguous matches count, and a check already recorded on another request is never reused.
    """
    used = {check_number(r['paid_ref']) for r in requests if r['status'] == 'Paid'} - {None}
    out, taken = [], set()
    for r in requests:
        if r['status'] != 'Submitted':
            continue
        filed = _day(r['submitted'])
        if not filed:
            continue
        earliest = filed - timedelta(days=MATCH_WINDOW_DAYS)
        who = r.get('payee_name') or r.get('submitter_name')
        targets = [t for t in (r.get('net'), r.get('total')) if t]
        checks = [p for p in payments if p['kind'] == 'check' and p['number'] and p['number'] not in used | taken
                  and (_day(p['date']) or earliest) >= earliest and _same_person(who, p['payee'])
                  and any(abs(p['amount'] - t) <= AMOUNT_SLACK for t in targets)]
        if len(checks) == 1:
            p = checks[0]
            taken.add(p['number'])
            out.append((r, {'paid_date': p['date'], 'paid_ref': f"Check {p['number']}", 'paid_amount': p['amount'],
                            'note': f"Matched to QuickBooks check {p['number']} to {p['payee']}"}))
            continue
        net = r.get('net') or 0
        if not net or round(net * 100) % 100 == 0:
            continue    # a round amount on a card statement is too likely to be something else
        hits = [(p, ln) for p in payments if is_card_statement(p) and (_day(p['date']) or earliest) >= earliest
                for ln in p['lines'] if abs(ln['amount'] - net) < 0.005 and (p['id'], ln['description']) not in taken]
        if len(hits) == 1:
            p, ln = hits[0]
            taken.add((p['id'], ln['description']))
            out.append((r, {'paid_date': p['date'], 'paid_ref': 'Region card', 'paid_amount': ln['amount'],
                            'note': f"Matched to the {p['date']} card statement line \"{ln['description'][:60]}\""}))
    return out


def exceptions(requests, payments, events, projects, today, since=None):
    """Things the treasurer should look at, as rows: key, kind, date, name, amount, detail, link.

    The key is stable across nights so a row marked as reviewed stays hidden.
    """
    since = since or date(today.year, 1, 1)
    out = []
    add = lambda key, kind, d, name, amount, detail, link='': out.append(  # noqa: E731
        {'key': key, 'kind': kind, 'date': str(d or ''), 'name': name, 'amount': amount, 'detail': detail, 'link': link})

    on_requests = {check_number(r['paid_ref']) for r in requests} - {None}
    numbers = {p['number'] for p in payments if p['kind'] == 'check' and p['number']}
    for p in payments:
        if p['kind'] == 'check' and p['number'] and p['number'] not in on_requests and (_day(p['date']) or since) >= since:
            what = '; '.join(ln['description'].split('\n')[0][:50] for ln in p['lines'][:2])
            add(f"check:{p['number']}", 'Check with no funds request', p['date'], p['payee'], p['amount'],
                f"Check {p['number']}" + (f": {what}" if what else ''))
    for r in requests:
        n = check_number(r['paid_ref'])
        if r['status'] == 'Paid' and n and n not in numbers and (_day(r['paid_date']) or since) >= since:
            add(f"frcheck:{r['id']}", 'Paid request not in QuickBooks', r['paid_date'], r.get('payee_name') or r.get('submitter_name'),
                r.get('paid_amount') or r.get('net'), f"{r['id']} says check {n}, but QuickBooks has no check {n}", r['id'])
        filed = _day(r['submitted'])
        if r['status'] == 'Submitted' and filed and (today - filed).days > WAITING_DAYS:
            add(f"frwait:{r['id']}", 'Funds request waiting over a month', r['submitted'], r.get('payee_name') or r.get('submitter_name'),
                r.get('net'), f"{r['id']} filed {(today - filed).days} days ago, not paid or rejected", r['id'])

    by_code = {p['code']: p for p in projects}
    codes = set()
    for e in events:
        code = e.get('custom_tag') or e.get('unique_code')
        codes.add(code)
        d = _day(e.get('date'))
        if not d or d > today or d < since:
            continue
        p = by_code.get(code)
        if not p:
            add(f"evproj:{code}", 'Event has no QuickBooks project', e['date'], e.get('event_name', ''), None,
                f"{code} ({e.get('program_code', '')}) has no QuickBooks project named with its code", code)
        elif not p['lines'] and (today - d).days > NO_ACTUALS_DAYS:
            add(f"evact:{code}", 'Event has no actuals yet', e['date'], e.get('event_name', ''), None,
                f"{code} ran {(today - d).days} days ago and nothing is booked to {p['project']}", code)
    for p in projects:
        if p['code'] not in codes and p.get('active', True) and p['lines']:
            add(f"projev:{p['code']}", 'QuickBooks project not in Event Register', '', p['project'], None,
                f"{p['code']} is not a registered event code", p['code'])
    return out
