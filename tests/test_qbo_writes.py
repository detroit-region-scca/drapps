import qbo_logic as Q

PAY = [{'kind': 'check', 'id': 'Purchase 11', 'number': '6001', 'date': '2026-11-01', 'payee': 'A Payee', 'amount': 50.0, 'lines': []},
       {'kind': 'other', 'id': 'Purchase 12', 'number': '', 'date': '2026-11-03', 'payee': 'Cardmember Service', 'amount': 99.0,
        'lines': [{'description': 'Store', 'amount': 12.34}, {'description': 'Other', 'amount': 86.66}]}]


def test_payment_of_check_and_card():
    assert Q.payment_of({'paid_ref': 'Check 6001'}, PAY) == ('Purchase', '11')
    assert Q.payment_of({'paid_ref': 'Region card', 'paid_amount': 12.34, 'paid_date': '2026-11-03'}, PAY) == ('Purchase', '12')
    assert Q.payment_of({'paid_ref': 'Region card', 'paid_amount': 12.34, 'paid_date': '2026-11-04'}, PAY) is None
    assert Q.payment_of({'paid_ref': 'Check 7000'}, PAY) is None
    assert Q.payment_of({'paid_ref': ''}, PAY) is None


def test_attach_type():
    assert Q.attach_type('r.PDF') == 'application/pdf'
    assert Q.attach_type('photo.heic') is None
    assert Q.attach_type('noext') is None


NAMES = ['Club AX Autocross Revenue', 'Club RX Rallycross Revenue', 'Operations Revenue', 'Sponsorship Revenue']


def test_income_account():
    assert Q.income_account('RX', NAMES) == 'Club RX Rallycross Revenue'
    assert Q.income_account('OPS', NAMES) == 'Operations Revenue'
    assert Q.income_account('ZZ', NAMES) == 'Operations Revenue'


INV = {'id': 'INV-2026-001', 'created': '2026-11-09', 'due': '2026-12-09', 'status': 'Open', 'program': 'AX', 'event_code': '#DR1',
       'event_name': 'Test Event', 'bill_name': 'Example Co', 'bill_email': '', 'bill_address': '1 Main', 'notes': '', 'created_by': 'X',
       'total': 100.0, 'lines': [{'description': 'Sponsor', 'quantity': 1, 'unit_price': 100.0, 'amount': 100.0}]}


def test_invoices_to_add_and_job():
    old = dict(INV, id='INV-2026-000', created='2026-10-01')
    void = dict(INV, id='INV-2026-002', status='Void')
    assert [i['id'] for i in Q.invoices_to_add([INV, old, void], set())] == ['INV-2026-001']
    assert Q.invoices_to_add([INV], {'INV-2026-001'}) == []
    j = Q.invoice_job(INV, NAMES)
    assert j['account'] == 'Club AX Autocross Revenue' and j['project'] == '#DR1' and j['doc'] == 'INV-2026-001'
    assert j['lines'][0]['amount'] == 100.0


def test_invoice_states():
    paid_q = [{'number': 'INV-2026-001', 'id': '9', 'total': 100.0, 'balance': 0.0, 'paid_date': '2026-11-20'}]
    paid, exc = Q.invoice_states([INV], paid_q, [])
    assert paid[0][1]['paid_date'] == '2026-11-20' and exc == []
    open_q = [dict(paid_q[0], balance=100.0, paid_date='')]
    dep = [{'id': 'Deposit 5', 'date': '2026-11-21', 'amount': 100.0, 'description': 'Example Co sponsorship', 'payer': ''}]
    paid, exc = Q.invoice_states([INV], open_q, dep)
    assert paid == [] and exc[0]['kind'] == 'Invoice money deposited as income'
    paid, exc = Q.invoice_states([dict(INV, status='Void')], open_q, [])
    assert exc[0]['kind'] == 'Invoice void in drapps, open in QuickBooks'


def test_invoice_states_flags_changes_and_deletes():
    q = [{'number': 'INV-2026-001', 'id': '9', 'total': 120.0, 'balance': 120.0, 'paid_date': ''},
         {'number': 'INV-2026-005', 'id': '10', 'total': 50.0, 'balance': 50.0, 'paid_date': ''}]
    paid, exc = Q.invoice_states([INV], q, [])
    kinds = sorted(e['kind'] for e in exc)
    assert kinds == ['Invoice deleted in drapps, open in QuickBooks', 'Invoice differs in QuickBooks']
    q[0]['total'] = 100.0
    paid, exc = Q.invoice_states([dict(INV, status='Paid')], q[:1], [])
    assert [e['kind'] for e in exc] == ['Invoice paid in drapps, open in QuickBooks']
