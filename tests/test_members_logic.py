from datetime import date, datetime

import members_logic as L

MEM = {'1': {'member': '1', 'first': 'Jane', 'last': 'Smith'}, '2': {'member': '2', 'first': 'PAT', 'last': 'QUINN'}}


def test_grid_rows_work_out_points_from_role_and_days():
    rows, problems = L.rows_from_grid([
        {'member': '1 Jane Smith', 'role': 'Worker', 'days': '2', 'points': '', 'note': 'registration'},
        {'member': '2 Pat Quinn', 'role': 'Event chair', 'days': '2', 'points': '', 'note': ''},
        {'member': '', 'role': 'Worker', 'days': '1', 'points': '', 'note': ''}], MEM, '#AX1', date(2026, 11, 2))
    assert problems == []
    assert [(r['member'], r['name'], r['points'], r['what'], r['kind']) for r in rows] == [
        ('1', 'Jane Smith', 2.0, 'Worker: registration', 'Earned'),
        ('2', 'Pat Quinn', 8.0, 'Event chair', 'Earned')]


def test_grid_keeps_typed_names_for_the_keeper_to_match_and_needs_points_for_role_points():
    rows, problems = L.rows_from_grid([{'member': 'Bob Jones', 'role': 'Worker', 'days': '1'},
                                       {'member': '1', 'role': 'Role points (volunteer job)', 'points': ''}], MEM, '', date(2026, 11, 2))
    assert rows[0]['member'] == '' and rows[0]['name'] == 'Bob Jones'
    assert problems == ['1: enter the points']


def test_spending_cannot_go_below_zero():
    rows = [{'member': '1', 'points': -12}]
    assert L.check_spending(rows, {'1': 10}) == ['1: has only 10 points']
    assert L.check_spending(rows, {'1': 12}) == []


def test_only_approved_entries_count_and_pending_group_by_sender():
    e = [{'member': '1', 'points': 4, 'status': 'Approved', 'event': '#A', 'date': date(2026, 9, 1), 'by': 'K'},
         {'member': '1', 'points': 2, 'status': 'Pending', 'event': '#B', 'date': date(2026, 10, 1), 'by': 'Chair'},
         {'member': '2', 'points': 1, 'status': 'Pending', 'event': '#B', 'date': date(2026, 10, 1), 'by': 'Chair'}]
    assert L.balances(e) == {'1': 4}
    groups = L.pending_groups(e)
    assert len(groups) == 1 and len(groups[0][1]['rows']) == 2


def test_roster_merge_refreshes_dates_adds_new_and_keeps_former_members():
    rows = [('Member ID', 'First Name', 'Last Name', 'Suffix', 'Address Line 1', 'SCCA Member Expire\n', 'Region Member Expire\n', 'Region Join Date', 'Birth Date'),
            (1, 'Jane', 'Smith', None, 'x', datetime(2027, 6, 30), '05/31/2027', '03/15/2016', datetime(1980, 1, 1)),
            (500200, 'Norris', 'Ames', None, 'x', '01/31/2027', None, '11/02/1991', None),
            (3, 'New', 'Person', 'Jr', 'x', '10/31/2027', None, '11/01/2026', None)]
    report = L.roster(rows)
    assert report['1'] == {'first': 'Jane', 'last': 'Smith', 'joined': date(2016, 3, 15), 'expires': date(2027, 6, 30)}
    members = [{'member': '1', 'first': 'Jane', 'last': 'Smith', 'life': False, 'points': 5.0},
               {'member': '500200-1', 'first': 'Norris', 'last': 'Ames', 'life': False, 'points': 29.0},
               {'member': '9', 'first': 'Gone', 'last': 'Away', 'life': False, 'points': 7.0, 'listed': date(2026, 1, 1)}]
    out = {m['member']: m for m in L.merge_roster(members, report, date(2026, 11, 2))}
    assert out['500200-1']['expires'] == date(2027, 1, 31) and out['500200-1']['listed'] == date(2026, 11, 2)
    assert L.canon('500100_0') == L.canon('500100') and L.canon('500200-1') == L.canon('500200_1')
    assert out['3']['last'] == 'Person Jr' and out['3']['points'] == 0
    assert out['9']['listed'] == date(2026, 1, 1)
    lapsed = L.lapsed(list(out.values()), date(2026, 11, 2))
    assert [m['member'] for m in lapsed] == ['9']
    ms = L.milestones(list(out.values()), '2026-11')
    assert ms == {35: ['Norris Ames'], 0: ['New Person Jr']}


def test_lapsed_is_empty_until_a_list_is_loaded():
    assert L.lapsed([{'member': '1', 'first': 'a', 'last': 'b', 'life': False, 'points': 5, 'listed': None}], date(2026, 11, 2)) == []


def test_month_books():
    e = [{'date': date(2026, 11, 3), 'points': 4.0, 'kind': 'Earned', 'event': '#X', 'status': 'Approved', 'name': 'A', 'what': 'w'},
         {'date': date(2026, 11, 4), 'points': 12.0, 'kind': 'Role points', 'event': '', 'status': 'Approved', 'name': 'B', 'what': 'r'},
         {'date': date(2026, 11, 9), 'points': -10.0, 'kind': 'Used', 'event': '', 'status': 'Approved', 'name': 'C', 'what': 'renewal'},
         {'date': date(2026, 11, 9), 'points': 99.0, 'kind': 'Earned', 'event': '#X', 'status': 'Pending', 'name': 'D', 'what': 'x'}]
    b = L.month_books(e, {'#X': {'program_code': 'AX'}}, 2026, 11)
    assert [(ln['debit'].split(':')[0], ln['amount']) for ln in b['lines']] == [('Club AX Autocross Expense', 20.0), ('Operations Expense', 60.0)]
    assert [x['name'] for x in b['used']] == ['C']


def test_standings_names_and_points_only():
    out = L.standings([{'member': '1', 'first': 'PAT', 'last': 'QUINN', 'points': 170.6}, {'member': '2', 'first': 'A', 'last': 'Zed', 'points': 0},
                       {'member': 'UNASSIGNED', 'first': '', 'last': 'Unassigned', 'points': 3}])
    assert out == [{'name': 'Pat Quinn', 'points': 170.6}]


def test_scca_number_formats_match_the_ledger():
    rows = [('Member ID', 'First Name', 'Last Name', 'SCCA Member Expire', 'Region Join Date'),
            ('500100_0', 'Dana', 'Byers', '07/31/2027', '01/01/2001'), ('500200_1', 'Norris', 'Ames', '05/31/2027', '11/02/1991')]
    members = [{'member': '500100', 'first': 'Dana', 'last': 'Byers', 'life': False, 'points': 13.0},
               {'member': '500200-1', 'first': 'Norris', 'last': 'Ames', 'life': False, 'points': 29.0}]
    out = L.merge_roster(members, L.roster(rows), date(2026, 11, 2))
    assert len(out) == 2 and all(m['listed'] == date(2026, 11, 2) for m in out)


def test_use_points_needs_enough_balance():
    from datetime import date
    m = {'member': '1', 'first': 'Pat', 'last': 'Quinn', 'points': 10.0}
    e, p = L.use_entry(m, '4', 'SCCA membership renewal', date(2026, 11, 2))
    assert p is None and e['points'] == -4.0 and e['kind'] == 'Used'
    assert L.use_entry(m, '12', 'x', date(2026, 11, 2))[1] == 'Pat Quinn has only 10 points.'
    assert L.use_entry(m, '0', 'x', date(2026, 11, 2))[1] == 'Enter how many points to use.'


def test_enter_points_rejects_negative_numbers():
    rows, problems = L.rows_from_grid([{'member': '1', 'role': 'Other', 'points': '-5'}], MEM, '', date(2026, 11, 2))
    assert rows == [] and 'Use points tab' in problems[0]
