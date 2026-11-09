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


CARD_REF = 'Region card'


def card_ref(payment_id):
    """'Region card (QuickBooks Purchase 12)': readable, and names the card statement so it is never matched twice."""
    return f"{CARD_REF} (QuickBooks {payment_id})"


def card_payment_id(paid_ref):
    """The QuickBooks id in a card paid_ref ('Purchase 12'), or None (typed by hand, or matched before ids were kept)."""
    m = re.search(r'\(QuickBooks (\w+ \w+)\)', paid_ref or '')
    return m.group(1) if m else None


def _card_lines_left(requests, payments):
    """Card statement lines no paid request has used yet: [(payment, line)]. A paid card request takes the line its
    ref names; one with no id (typed by hand, or older) takes a line of its exact amount, on its paid date if any."""
    left = [(p, ln) for p in payments if is_card_statement(p) for ln in p['lines']]
    paid = [r for r in requests if r['status'] == 'Paid' and 'card' in (r.get('paid_ref') or '').lower() and r.get('paid_amount')]
    for r in sorted(paid, key=lambda r: not card_payment_id(r['paid_ref'])):     # ones that name their statement first
        pid = card_payment_id(r['paid_ref'])
        same = [x for x in left if abs(x[1]['amount'] - r['paid_amount']) < 0.005 and (not pid or x[0]['id'] == pid)]
        same.sort(key=lambda x: x[0]['date'] != str(r.get('paid_date') or ''))
        if same:
            left = [x for x in left if x is not same[0]]
    return left


def match_funds(requests, payments):
    """Open requests that QuickBooks shows as paid: [(request, {'paid_date', 'paid_ref', 'paid_amount', 'note'})].

    A check matches on the payee's last name and the amount (within a dollar), dated no earlier than shortly
    before the request. A region card charge matches on the exact amount (with cents) on a card statement line.
    Only unambiguous matches count. A check or card line already recorded on a paid request (tonight or on an
    earlier night) is never used again.
    """
    used = {check_number(r['paid_ref']) for r in requests if r['status'] == 'Paid'} - {None}
    card_left = _card_lines_left(requests, payments)
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
        hits = [x for x in card_left if (_day(x[0]['date']) or earliest) >= earliest and abs(x[1]['amount'] - net) < 0.005]
        if len(hits) == 1:
            p, ln = hits[0]
            card_left = [x for x in card_left if x is not hits[0]]
            out.append((r, {'paid_date': p['date'], 'paid_ref': card_ref(p['id']), 'paid_amount': ln['amount'],
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
        # A voided check stays in QuickBooks at $0; it is not a payment anyone needs a request for.
        if p['kind'] == 'check' and p['number'] and p['amount'] and p['number'] not in on_requests and (_day(p['date']) or since) >= since:
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
            add(f"projev:{p['code']}", 'QuickBooks project not in Events', '', p['project'], None,
                f"{p['code']} is not a registered event code", p['code'])
    return out


def deposit_ref(deposit_id, day):
    """'QuickBooks deposit 2026-10-20 #123': readable, and names the deposit so it is never matched twice."""
    return f"QuickBooks deposit {day} #{str(deposit_id).rpartition(' ')[2]}"


def deposit_id(ref):
    """The QuickBooks deposit a paid_ref or deposit_ref names ('Deposit 123'), or None (typed by hand, or matched
    before ids were kept)."""
    m = re.search(r'QuickBooks deposit \S+ #(\w+)', ref or '')
    return f'Deposit {m.group(1)}' if m else None


def unused_deposits(deposits, taken):
    """The deposit lines nothing has been matched to yet.

    taken: (ref, date, amount) for every invoice already Paid and every money received report already Deposited.
    Each takes one line of its amount, or a whole deposit that adds up to it: from the deposit its ref names, or
    for a ref with no id (typed by hand, or older) from the deposits of its date. So a deposit matched on an
    earlier night is not matched again tonight."""
    left = list(deposits)
    for ref, day, amount in sorted(taken, key=lambda t: not deposit_id(t[0])):     # ones that name their deposit first
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            continue
        did = deposit_id(ref)
        pool = [d for d in left if (d['id'] == did if did else d['date'] == str(day or '')[:10])]
        line = next((d for d in pool if abs(d['amount'] - amount) < 0.005), None)
        if line:
            left = [d for d in left if d is not line]
            continue
        for i in {d['id'] for d in pool}:
            if abs(sum(d['amount'] for d in pool if d['id'] == i) - amount) < 0.005:
                left = [d for d in left if d['id'] != i]
                break
    return left


def match_invoices(invoices, deposits, taken=()):
    """Open invoices a QuickBooks deposit paid: [(invoice, {'paid_date', 'paid_ref', 'paid_amount'})].

    A deposit line matches on the exact total, dated on or after the invoice, and naming the payer (any word of
    three letters or more from "Bill to" in the deposit's payer or description) or the invoice number. Only
    unambiguous matches count: one line for the invoice, and no other open invoice that line could pay (unless it
    names this invoice's number). A line is used once, and never one an invoice paid on an earlier night used.
    taken: (ref, date, amount) of money received reports already deposited, which used deposits too.
    """
    deposits = unused_deposits(deposits, [(i.get('paid_ref'), i.get('paid_date'), i.get('paid_amount'))
                                          for i in invoices if i['status'] == 'Paid'] + list(taken))
    text = lambda d: (d['description'] + ' ' + d['payer']).lower()  # noqa: E731
    open_ = sorted((i for i in invoices if i['status'] == 'Open' and i['total']), key=lambda i: (i['created'], i['id']))
    cands = {}
    for inv in open_:
        words = {w for w in re.findall(r'[a-z]+', inv['bill_name'].lower()) if len(w) >= 3} - {'inc', 'llc', 'the', 'and', 'company', 'corp'}
        cands[inv['id']] = [n for n, d in enumerate(deposits) if abs(d['amount'] - inv['total']) < 0.005 and d['date'] >= inv['created'][:10]
                            and (inv['id'].lower() in text(d) or words & set(re.findall(r'[a-z]+', text(d))))]
    out, used = [], set()
    for inv in open_:
        hits = [n for n in cands[inv['id']] if n not in used]
        named = [n for n in hits if inv['id'].lower() in text(deposits[n])]
        hits = named or hits
        if len(hits) == 1 and (named or not any(hits[0] in c for i, c in cands.items() if i != inv['id'])):
            d = deposits[hits[0]]
            used.add(hits[0])
            out.append((inv, {'paid_date': d['date'], 'paid_ref': deposit_ref(d['id'], d['date']), 'paid_amount': d['amount']}))
    return out


# Receipt files QuickBooks takes as attachments (tools/qbo_write.mjs).
ATTACH_TYPES = {'pdf': 'application/pdf', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif',
                'tif': 'image/tiff', 'tiff': 'image/tiff', 'csv': 'text/csv', 'txt': 'text/plain', 'rtf': 'application/rtf',
                'doc': 'application/msword', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'xls': 'application/vnd.ms-excel', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}


def attach_type(name):
    """The content type QuickBooks takes for a receipt file, or None (QuickBooks refuses HEIC, ZIP and the like)."""
    return ATTACH_TYPES.get(name.rsplit('.', 1)[-1].lower()) if '.' in name else None


def payment_of(r, payments):
    """The QuickBooks transaction a paid request was matched to: ('Purchase', '123') or None.

    A check by its number; a region card charge by the statement its ref names, or for an older ref with no id the
    statement that has a line of that amount on the paid date."""
    n = check_number(r.get('paid_ref'))
    if n:
        hits = [p for p in payments if p['kind'] == 'check' and p['number'] == n]
    elif (r.get('paid_ref') or '').startswith(CARD_REF) and r.get('paid_amount'):
        pid = card_payment_id(r['paid_ref'])
        hits = [p for p in payments if is_card_statement(p) and (p['id'] == pid if pid else p['date'] == str(r.get('paid_date')))
                and any(abs(ln['amount'] - r['paid_amount']) < 0.005 for ln in p['lines'])]
    else:
        return None
    if len(hits) != 1:
        return None
    kind, _, txn = hits[0]['id'].partition(' ')
    return kind, txn

