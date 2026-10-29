from datetime import date

import qbo_logic as Q


def req(id, payee, net, submitted='2026-06-01', status='Submitted', paid_ref='', paid_date=''):
    return {'id': id, 'payee_name': payee, 'submitter_name': '', 'net': net, 'total': net, 'status': status,
            'submitted': submitted, 'paid_ref': paid_ref, 'paid_date': paid_date, 'paid_amount': ''}


def chk(number, payee, amount, d='2026-06-08', lines=()):
    return {'kind': 'check', 'id': f'Purchase {number}', 'number': number, 'payee': payee, 'amount': amount, 'date': d,
            'lines': [{'description': x, 'amount': amount} for x in lines]}


def card(d, lines):
    return {'kind': 'other', 'id': f'Purchase c{d}', 'number': '', 'payee': 'Cardmember Services - Comerica CC',
            'amount': sum(a for _, a in lines), 'date': d, 'lines': [{'description': x, 'amount': a} for x, a in lines]}


def test_program_of_accounts():
    assert Q.program_of('Club AX Autocross Site Rental') == 'AX'
    assert Q.program_of('Club DR General Events Revenue') == 'DR'
    assert Q.program_of('Operations Storage') == 'OPS'
    assert Q.program_of('Other Investment Income') == 'OPS'


def test_program_totals_net():
    t = Q.program_totals([{'section': 'Income', 'account': 'Club RX Rallycross Revenue', 'amount': 100.0},
                          {'section': 'Expense', 'account': 'Club RX Rallycross Supplies', 'amount': 30.5}])
    assert t['RX']['net'] == 69.5 and t['RX']['income'] == 100.0


def test_check_number_formats():
    assert Q.check_number('Check 5732') == '5732'
    assert Q.check_number('check #6010') == '6010'
    assert Q.check_number('Region card (10/2026 statement)') is None


def test_check_matches_surname_and_amount():
    r = req('FR-1', 'Bob Jones', 208.6)
    m = Q.match_funds([r], [chk('6057', 'Robert Jones', 208.60)])
    assert m and m[0][1]['paid_ref'] == 'Check 6057'


def test_check_must_be_unambiguous_and_unused():
    r = req('FR-1', 'Pat Smith', 100)
    two = [chk('6001', 'Pat Smith', 100), chk('6002', 'Pat Smith', 100.5)]
    assert not Q.match_funds([r], two)
    paid = req('FR-0', 'Pat Smith', 100, status='Paid', paid_ref='Check 6001')
    assert Q.match_funds([paid, r], two)[0][1]['paid_ref'] == 'Check 6002'


def test_check_wrong_person_or_too_early_does_not_match():
    r = req('FR-1', 'Lee Brown', 327.06, submitted='2026-06-01')
    assert not Q.match_funds([r], [chk('9', 'Kim Green', 327.06)])
    assert not Q.match_funds([r], [chk('9', 'Lee Brown', 327.06, d='2026-05-01')])


def test_card_line_exact_cents_only():
    r = req('FR-1', 'Sam', 175.23)
    m = Q.match_funds([r], [card('2026-06-26', [('Sam - cones', 175.23), ('ionos', 15.62)])])
    assert m[0][1]['paid_ref'] == 'Region card'
    assert not Q.match_funds([req('FR-2', 'Sam', 175)], [card('2026-06-26', [('constant contact', 175)])])


def test_exceptions():
    today = date(2026, 10, 29)
    reqs = [req('FR-1', 'A', 10, status='Paid', paid_ref='Check 7000', paid_date='2026-05-01'),
            req('FR-2', 'B', 20, submitted='2026-08-01')]
    pays = [chk('6001', 'Radisson', 50, d='2026-03-07'), chk('6002', 'A', 10)]
    reqs.append(req('FR-3', 'A', 10, status='Paid', paid_ref='Check 6002', paid_date='2026-06-08'))
    events = [{'unique_code': '#DRAAA', 'custom_tag': '', 'date': '2026-06-01', 'event_name': 'X', 'program_code': 'AX'},
              {'unique_code': '#DRBBB', 'custom_tag': '', 'date': '2026-09-01', 'event_name': 'Y', 'program_code': 'RX'},
              {'unique_code': '#DRCCC', 'custom_tag': '', 'date': '2026-12-01', 'event_name': 'Z', 'program_code': 'RX'}]
    projects = [{'code': '#DRBBB', 'project': 'Y #DRBBB', 'lines': []},
                {'code': '#DRZZZ', 'project': 'Old #DRZZZ', 'lines': [{'amount': 1}]}]
    keys = {e['key'] for e in Q.exceptions(reqs, pays, events, projects, today)}
    assert keys == {'check:6001', 'frcheck:FR-1', 'frwait:FR-2', 'evproj:#DRAAA', 'evact:#DRBBB', 'projev:#DRZZZ'}
