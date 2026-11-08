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


def match_invoices(invoices, deposits):
    """Open invoices a QuickBooks deposit paid: [(invoice, {'paid_date', 'paid_ref', 'paid_amount'})].

    A deposit line matches on the exact total, dated on or after the invoice, and naming the payer (any word of
    three letters or more from "Bill to" in the deposit's payer or description) or the invoice number. Only
    unambiguous matches count, and a deposit line is used once.
    """
    out, used = [], set()
    for inv in invoices:
        if inv['status'] != 'Open' or not inv['total']:
            continue
        words = {w for w in re.findall(r'[a-z]+', inv['bill_name'].lower()) if len(w) >= 3} - {'inc', 'llc', 'the', 'and', 'company', 'corp'}
        hits = [d for d in deposits if (d['id'], d['description'], d['amount']) not in used and abs(d['amount'] - inv['total']) < 0.005
                and d['date'] >= inv['created'][:10]
                and (inv['id'].lower() in (d['description'] + ' ' + d['payer']).lower()
                     or words & set(re.findall(r'[a-z]+', (d['description'] + ' ' + d['payer']).lower())))]
        if len(hits) == 1:
            d = hits[0]
            used.add((d['id'], d['description'], d['amount']))
            out.append((inv, {'paid_date': d['date'], 'paid_ref': f"QuickBooks deposit {d['date']}", 'paid_amount': d['amount']}))
    return out


# QuickBooks writes (tools/qbo_write.mjs). Invoices made in drapps before this date were never put in QuickBooks
# (their money was booked as deposits), so only newer ones go in, or the income would count twice.
INVOICES_FROM = '2026-11-08'
ATTACH_TYPES = {'pdf': 'application/pdf', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif',
                'tif': 'image/tiff', 'tiff': 'image/tiff', 'csv': 'text/csv', 'txt': 'text/plain', 'rtf': 'application/rtf',
                'doc': 'application/msword', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'xls': 'application/vnd.ms-excel', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}


def attach_type(name):
    """The content type QuickBooks takes for a receipt file, or None (QuickBooks refuses HEIC, ZIP and the like)."""
    return ATTACH_TYPES.get(name.rsplit('.', 1)[-1].lower()) if '.' in name else None


def payment_of(r, payments):
    """The QuickBooks transaction a paid request was matched to: ('Purchase', '123') or None.

    A check by its number; a region card charge by the statement that has a line of that amount on the paid date."""
    n = check_number(r.get('paid_ref'))
    if n:
        hits = [p for p in payments if p['kind'] == 'check' and p['number'] == n]
    elif r.get('paid_ref') == 'Region card' and r.get('paid_amount'):
        hits = [p for p in payments if is_card_statement(p) and p['date'] == str(r.get('paid_date'))
                and any(abs(ln['amount'] - r['paid_amount']) < 0.005 for ln in p['lines'])]
    else:
        return None
    if len(hits) != 1:
        return None
    kind, _, txn = hits[0]['id'].partition(' ')
    return kind, txn


def income_account(program, names):
    """The income account a program's invoices go to: its "Club XX ... Revenue" account, else Operations Revenue."""
    hit = [n for n in names if re.match(rf'Club {re.escape(program)} .*Revenue$', n)]
    return hit[0] if len(hit) == 1 else 'Operations Revenue'


def invoice_job(inv, names):
    """The qbo_write.mjs job that puts a drapps invoice in QuickBooks."""
    what = (inv.get('event_name') or '') + (f" {inv['event_code']}" if inv.get('event_code') else '')
    return {'op': 'invoice', 'ref': inv['id'], 'doc': inv['id'], 'date': inv['created'][:10], 'due': inv['due'][:10],
            'customer': {'name': inv['bill_name'], 'email': inv.get('bill_email') or '', 'address': inv.get('bill_address') or ''},
            'project': inv.get('event_code') or '', 'account': income_account(inv['program'], names),
            'memo': inv.get('notes') or '',
            'note': f"From drapps {inv['id']} for {inv['bill_name']}" + (f", {what.strip()}" if what.strip() else '')
                    + (f", by {inv['created_by']}" if inv.get('created_by') else '') + '. The deposit should be matched to this invoice.',
            'lines': [{'description': ln['description'], 'quantity': ln['quantity'] or 1, 'unit_price': ln['unit_price'], 'amount': ln['amount']}
                      for ln in inv['lines']]}


def invoices_to_add(invoices, in_qbo):
    """drapps invoices that belong in QuickBooks and are not there yet: open or paid, made since INVOICES_FROM."""
    return [inv for inv in invoices if inv['status'] in ('Open', 'Paid') and inv['created'][:10] >= INVOICES_FROM
            and inv['id'] not in in_qbo]


def invoice_states(invoices, app_invoices, deposits):
    """What QuickBooks says about drapps invoices that are in it: (paid, exceptions).

    paid: [(invoice, {'paid_date', 'paid_ref', 'paid_amount'})] for open ones QuickBooks shows fully paid.
    exceptions: rows in the exceptions format, for a deposit that looks like the invoice's money booked straight to
    income (it would count twice) and for an invoice voided in drapps but still owed in QuickBooks."""
    q = {i['number']: i for i in app_invoices}
    paid, exc = [], []
    for inv in invoices:
        x = q.get(inv['id'])
        if not x:
            continue
        if abs((inv['total'] or 0) - x['total']) >= 0.005:
            exc.append({'key': f"invdiff:{inv['id']}:{inv['total']}:{x['total']}", 'kind': 'Invoice differs in QuickBooks', 'date': inv['created'][:10],
                        'name': inv['bill_name'], 'amount': inv['total'],
                        'detail': f"{inv['id']} is ${inv['total'] or 0:,.2f} in drapps but ${x['total']:,.2f} in QuickBooks "
                                  '(changed by hand, or a deleted invoice number used again). Fix QuickBooks to match.', 'link': inv['id']})
            continue
        if inv['status'] == 'Paid' and x['balance'] >= 0.005:
            exc.append({'key': f"invpaid:{inv['id']}", 'kind': 'Invoice paid in drapps, open in QuickBooks', 'date': str(inv.get('paid_date') or ''),
                        'name': inv['bill_name'], 'amount': x['balance'],
                        'detail': f"{inv['id']} is marked paid in drapps; receive the payment against it in QuickBooks", 'link': inv['id']})
        if inv['status'] == 'Open' and x['balance'] < 0.005 and x['paid_date']:
            paid.append((inv, {'paid_date': x['paid_date'], 'paid_ref': 'QuickBooks payment', 'paid_amount': x['total']}))
        elif inv['status'] == 'Void' and x['balance'] >= 0.005:
            exc.append({'key': f"invvoid:{inv['id']}", 'kind': 'Invoice void in drapps, open in QuickBooks', 'date': inv['created'][:10],
                        'name': inv['bill_name'], 'amount': x['balance'], 'detail': f"Void {inv['id']} in QuickBooks too", 'link': inv['id']})
        if x['balance'] >= 0.005 and inv['status'] != 'Void':
            for _, hit in match_invoices([dict(inv, status='Open')], deposits):
                exc.append({'key': f"invdouble:{inv['id']}:{hit['paid_date']}", 'kind': 'Invoice money deposited as income',
                            'date': hit['paid_date'], 'name': inv['bill_name'], 'amount': hit['paid_amount'],
                            'detail': f"A {hit['paid_date']} deposit looks like {inv['id']}, but the invoice is still open in QuickBooks. "
                                      'Match the deposit to the invoice in Banking instead, or the income counts twice.', 'link': inv['id']})
    ours = {inv['id'] for inv in invoices}
    for x in app_invoices:
        if x['number'] not in ours and x['balance'] >= 0.005:
            exc.append({'key': f"invgone:{x['number']}:{x['id']}", 'kind': 'Invoice deleted in drapps, open in QuickBooks', 'date': '',
                        'name': x['number'], 'amount': x['balance'],
                        'detail': f"{x['number']} is no longer in drapps but is still owed in QuickBooks. Void or delete it there.", 'link': ''})
    return paid, exc
