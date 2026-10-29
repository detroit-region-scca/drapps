from datetime import date

import wp_logic as L

EVENTS = {'#DR9H6': {'program_code': 'RY'}, '#DRDQ9': {'program_code': 'RX'}, '#DRJEA': {'program_code': 'AX'}}


def t(d, typ, pts, code=''):
    return {'date': d, 'type': typ, 'points': pts, 'event_code': code, 'member': '1', 'name': 'A', 'description': ''}


def test_day_suffix_shares_event_program():
    assert L.program_for('#DR9H6-FRI', EVENTS) == 'RY'
    assert L.program_for('AC-2026-10', EVENTS) == 'OPS'


def test_allocation_rates_by_season():
    keeper = {'season': 2, 'offseason': 1}
    assert L.allocation_points(keeper, 4) == 2 and L.allocation_points(keeper, 10) == 2
    assert L.allocation_points(keeper, 11) == 1 and L.allocation_points(keeper, 3) == 1


def test_membership_lapsed():
    today = date(2026, 10, 28)
    assert L.membership_lapsed('9/26', today)
    assert not L.membership_lapsed('10/26', today)   # good through the end of October
    assert not L.membership_lapsed('5/27', today)
    assert not L.membership_lapsed('', today)


def test_forfeit_candidates_skip_life_and_zero():
    today = date(2026, 10, 28)
    mem = [{'member': '1', 'last': 'A', 'exp': '8/26', 'balance': 5, 'life': False},
           {'member': '2', 'last': 'B', 'exp': '8/26', 'balance': 5, 'life': True},
           {'member': '3', 'last': 'C', 'exp': '8/26', 'balance': 0, 'life': False},
           {'member': '4', 'last': 'D', 'exp': '12/26', 'balance': 5, 'life': False}]
    assert [m['member'] for m in L.forfeit_candidates(mem, today)] == ['1']


def test_month_end_entry():
    tx = [t(date(2026, 9, 11), 'Earned', 13, '#DR9H6-FRI'), t(date(2026, 9, 12), 'Earned', 13, '#DR9H6-SAT'),
          t(date(2026, 9, 1), 'Allocation', 5, 'AC-2026-09'), t(date(2026, 9, 15), 'Forfeit', -4),
          t(date(2026, 9, 20), 'Used', -10), t(date(2026, 8, 30), 'Earned', 99, '#DRJEA')]
    s = L.month_end_summary(tx, EVENTS, 2026, 9)
    by = {l['what']: l for l in s['lines']}
    assert by['RY points earned']['amount'] == 130.0
    assert by['RY points earned']['debit'].endswith('Road Rally Worker Points')
    assert by['OPS points earned']['amount'] == 25.0
    assert by['Points forfeited (memberships lapsed or moved)']['amount'] == 20.0
    assert s['used_points'] == -10 and s['net_change'] == round((26 + 5 - 4 - 10) * 5, 2)


def test_validate_entry():
    bal = {'1': 10.0}
    assert L.validate_entry('1', 3, 'Earned', bal) is None
    assert 'not in the ledger' in L.validate_entry('9', 3, 'Earned', bal)
    assert 'must be negative' in L.validate_entry('1', 3, 'Used', bal)
    assert 'only 10.0' in L.validate_entry('1', -11, 'Used', bal)
    assert L.validate_entry('1', -10, 'Used', bal) is None
