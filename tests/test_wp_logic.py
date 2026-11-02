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


def test_street_survival_points_book_to_its_own_account():
    assert L.PROGRAM_ACCOUNT['SS'] == 'Club SS Street Survival Expense:Club SS Street Survival Worker Points'


def test_role_points_follow_the_form():
    assert [L.role_points(r) for r in ('Event Chair', 'chief', 'Asst chair', 'special event', 'worker', '')] == [4, 2, 2, 3, 1, 1]


def test_parse_report_matches_members_and_works_out_points():
    members = [{'member': '1', 'first': 'Jane', 'last': 'Smith'}, {'member': '2', 'first': 'Robert', 'last': 'Jones'},
               {'member': '3', 'first': 'Al', 'last': 'Chan'}]
    rows = L.parse_report('Jane Smith, worker, 2\nBob Jones, chief, 1\nAl Chan, event chair, 2\nNo Body, worker\n\n', members)
    assert [(r['member'], r['points'], r['match']) for r in rows] == [
        ('1 Jane Smith', 2, 'exact'), ('', 2, ''), ('3 Al Chan', 8, 'exact'), ('', 1, '')]
    assert rows[1]['label'] == 'Bob Jones'     # Bob vs Robert: initials differ, so the keeper picks


def test_public_standings_names_and_points_only():
    from datetime import date
    members = [{'member': '1', 'first': 'ED', 'last': 'ARTHUR', 'balance': 170.6},
               {'member': '2', 'first': 'Ann', 'last': 'Able', 'balance': 0.0},
               {'member': '3', 'first': 'Bob', 'last': 'McBaker', 'balance': 2.04}]
    out = L.public_standings(members, [{'date': date(2026, 9, 12)}, {'date': None}])
    assert out['as_of'] == date(2026, 9, 12)
    assert [r['name'] for r in out['members']] == ['Ed Arthur', 'Bob McBaker']
    assert set(out['members'][0]) == {'name', 'last', 'first', 'points'}


def test_allocations_post_once_per_month():
    from datetime import date
    roles = [{'role': 'Points keeper', 'member': '1', 'name': 'A B', 'season': 4, 'offseason': 2}]
    rows = L.allocation_rows(roles, [], date(2026, 12, 1))
    assert [(r['points'], r['event_code'], r['date']) for r in rows] == [(2.0, 'AC-2026-12', date(2026, 12, 1))]
    assert L.allocation_rows(roles, [{'type': 'Allocation', 'event_code': 'AC-2026-12'}], date(2026, 12, 1)) == []


def test_month_end_email_lists_the_journal_entry():
    from datetime import date
    tx = [{'date': date(2026, 11, 3), 'points': 4.0, 'type': 'Earned', 'event_code': '#X', 'name': 'A', 'description': 'd'},
          {'date': date(2026, 11, 9), 'points': -10.0, 'type': 'Used', 'event_code': 'USED', 'name': 'B', 'description': 'renewal'}]
    s = L.month_end_summary(tx, {'#X': {'program_code': 'AX'}}, 2026, 11)
    subject, body = L.month_end_email(s, 2026, 11, {'difference': 0.0})
    assert subject == 'Worker points month end: November 2026'
    assert 'Debit Club AX Autocross Expense:Club AX Autocross Worker Points, credit Reserve Fund - Worker Awards: $20.00' in body
    assert 'B, renewal, -10 points' in body and 'ties to QuickBooks' in body


def test_lapsed_from_roster_uses_the_scca_list():
    from datetime import date
    members = [{'member': '1', 'first': 'A', 'last': 'Current', 'balance': 5, 'life': False},
               {'member': '2', 'first': 'B', 'last': 'Expired', 'balance': 5, 'life': False},
               {'member': '3', 'first': 'C', 'last': 'Gone', 'balance': 5, 'life': False},
               {'member': '4', 'first': 'D', 'last': 'Life', 'balance': 5, 'life': True},
               {'member': '500314-1', 'first': 'E', 'last': 'Family', 'balance': 5, 'life': False},
               {'member': '5', 'first': 'F', 'last': 'Empty', 'balance': 0, 'life': False}]
    expiry = {'1': date(2027, 6, 30), '2': date(2026, 9, 30), '500314': date(2027, 1, 31)}
    out = L.lapsed_from_roster(members, expiry, date(2026, 11, 2))
    assert [(m['last'], m['roster_exp']) for m in out] == [('Gone', None), ('Expired', date(2026, 9, 30))]


def test_roster_expiry_takes_the_later_date():
    import sa_logic as SA
    from datetime import date, datetime
    rows = [('Member ID', 'SCCA Member Expire\n', 'Region Member Expire\n'), (500123, datetime(2027, 6, 30), '05/31/2027'), ('9', None, None)]
    assert SA.roster_expiry(rows) == {'500123': date(2027, 6, 30)}
