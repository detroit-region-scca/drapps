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

