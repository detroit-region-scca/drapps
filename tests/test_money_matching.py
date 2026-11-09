"""Nightly QuickBooks matching across nights: a deposit, a card line or a check pays one thing, once. Made-up names only."""
import qbo_logic as Q
import received_store as R


def dep(i, d, amount, desc='', payer=''):
    return {'id': f'Deposit {i}', 'date': d, 'amount': amount, 'description': desc, 'payer': payer}


def rep(id='CR-2026-001', **kw):
    return dict({'id': id, 'status': 'Received', 'received': '2026-10-17', 'total': 200.0, 'cash': 200.0,
                 'deposit_ref': '', 'deposit_date': '', 'deposit_amount': ''}, **kw)


def inv(id='INV-2026-001', **kw):
    return dict({'id': id, 'status': 'Open', 'total': 500.0, 'created': '2026-03-01', 'bill_name': 'Example Motorsports LLC',
                 'paid_ref': '', 'paid_date': '', 'paid_amount': None}, **kw)


def night(reps, deposits):
    """One nightly run: mark what matched, as tools/qbo_nightly.py does."""
    for r, d in R.match_deposits(reps, deposits):
        r.update(status='Deposited', **d)
    return [r['status'] for r in reps]


def test_the_ref_names_the_quickbooks_deposit_and_reads_back():
    assert Q.deposit_ref('Deposit 123', '2026-10-20') == 'QuickBooks deposit 2026-10-20 #123'
    assert Q.deposit_id('QuickBooks deposit 2026-10-20 #123') == 'Deposit 123'
    assert Q.deposit_id('QuickBooks deposit 2026-10-20') is None and Q.deposit_id('Bank deposit') is None


def test_one_deposit_is_not_used_again_on_a_later_night():
    deps = [dep(7, '2026-10-20', 200.0)]
    first = rep()
    assert night([first], deps) == ['Deposited'] and first['deposit_ref'].endswith('#7')
    second = rep('CR-2026-002', received='2026-10-18')      # another $200 report, filed the next day
    assert night([first, second], deps) == ['Deposited', 'Received']
    assert night([first, second], deps + [dep(8, '2026-10-22', 200.0)]) == ['Deposited', 'Deposited']
    assert second['deposit_ref'].endswith('#8')


def test_a_report_matched_before_ids_were_kept_still_holds_its_deposit():
    old = rep(status='Deposited', deposit_ref='QuickBooks deposit 2026-10-20', deposit_date='2026-10-20', deposit_amount=200.0)
    new = rep('CR-2026-002')
    assert night([old, new], [dep(7, '2026-10-20', 200.0)]) == ['Deposited', 'Received']


def test_a_whole_deposit_used_by_one_report_is_gone_for_the_next():
    whole = rep(total=300.0, status='Deposited', deposit_ref='QuickBooks deposit 2026-10-20 #7', deposit_date='2026-10-20', deposit_amount=300.0)
    deps = [dep(7, '2026-10-20', 100.0), dep(7, '2026-10-20', 200.0)]
    assert night([whole, rep('CR-2026-002')], deps) == ['Deposited', 'Received']


def test_an_invoice_paid_on_an_earlier_night_keeps_its_deposit_line():
    deps = [dep(3, '2026-03-27', 500.0, 'Example sponsorship')]
    a = inv()
    (_, paid), = Q.match_invoices([a], deps)
    assert paid['paid_ref'] == 'QuickBooks deposit 2026-03-27 #3'
    a.update(status='Paid', **paid)
    b = inv('INV-2026-002', created='2026-03-05')
    assert not Q.match_invoices([b, a], deps)
    old = inv(status='Paid', paid_ref='QuickBooks deposit 2026-03-27', paid_date='2026-03-27', paid_amount=500.0)   # no id in the ref
    assert not Q.match_invoices([b, old], deps)


def test_two_open_invoices_one_deposit_pays_neither_unless_it_names_one():
    a, b = inv(), inv('INV-2026-002', created='2026-03-05')
    assert not Q.match_invoices([b, a], [dep(3, '2026-03-27', 500.0, 'Example sponsorship')])
    m = Q.match_invoices([b, a], [dep(3, '2026-03-27', 500.0, 'Example INV-2026-002')])
    assert [i['id'] for i, _ in m] == ['INV-2026-002']


def test_invoices_and_money_received_never_share_a_deposit():
    deps = [dep(3, '2026-10-20', 200.0, 'Example sponsorship')]
    paid = inv(total=200.0, status='Paid', paid_ref='QuickBooks deposit 2026-10-20 #3', paid_date='2026-10-20', paid_amount=200.0)
    taken = [(paid['paid_ref'], paid['paid_date'], paid['paid_amount'])]
    assert not R.match_deposits([rep()], deps, taken=taken)
    done = rep(status='Deposited', deposit_ref='QuickBooks deposit 2026-10-20 #3', deposit_date='2026-10-20', deposit_amount=200.0)
    assert not Q.match_invoices([inv(total=200.0, created='2026-10-01')], deps,
                                taken=[(done['deposit_ref'], done['deposit_date'], done['deposit_amount'])])


def req(id, net, status='Submitted', **kw):
    return dict({'id': id, 'payee_name': 'Sam Sample', 'submitter_name': '', 'net': net, 'total': net, 'status': status,
                 'submitted': '2026-06-01', 'paid_ref': '', 'paid_date': '', 'paid_amount': ''}, **kw)


def card(i, d, lines):
    return {'kind': 'other', 'id': f'Purchase {i}', 'number': '', 'payee': 'Cardmember Services', 'amount': sum(a for _, a in lines),
            'date': d, 'lines': [{'description': x, 'amount': a} for x, a in lines]}


def test_a_card_line_is_not_used_again_on_a_later_night():
    pay = [card(41, '2026-06-26', [('cones', 175.23), ('web', 15.62)])]
    a = req('FR-1', 175.23)
    (_, paid), = Q.match_funds([a], pay)
    assert paid['paid_ref'] == 'Region card (QuickBooks Purchase 41)' and Q.card_payment_id(paid['paid_ref']) == 'Purchase 41'
    a.update(status='Paid', paid_ref=paid['paid_ref'], paid_date=paid['paid_date'], paid_amount=paid['paid_amount'])
    b = req('FR-2', 175.23)
    assert not Q.match_funds([a, b], pay)
    old = req('FR-0', 175.23, status='Paid', paid_ref='Region card', paid_date='2026-06-26', paid_amount=175.23)   # no id in the ref
    assert not Q.match_funds([old, b], pay)
    two = pay + [card(42, '2026-07-26', [('more cones', 175.23)])]
    assert Q.match_funds([a, b], two)[0][1]['paid_ref'] == 'Region card (QuickBooks Purchase 42)'


def test_receipts_still_find_the_card_statement_by_id_or_by_date():
    pay = [card(41, '2026-06-26', [('cones', 175.23)]), card(42, '2026-06-26', [('cones again', 175.23)])]
    assert Q.payment_of({'paid_ref': 'Region card (QuickBooks Purchase 42)', 'paid_amount': 175.23, 'paid_date': '2026-06-26'}, pay) == ('Purchase', '42')
    assert Q.payment_of({'paid_ref': 'Region card', 'paid_amount': 175.23, 'paid_date': '2026-06-26'}, pay) is None    # two fit: not guessed
