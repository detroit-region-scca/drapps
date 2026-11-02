from datetime import date, datetime

import members_logic as L

MEM = {'1': {'member': '1', 'first': 'Jane', 'last': 'Smith'}, '2': {'member': '2', 'first': 'PAT', 'last': 'QUINN'}}


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


def test_auto_add_prepares_each_month_once():
    rows = [{'member': '1', 'name': 'Pat Quinn', 'role': 'Points keeper', 'season': 2, 'offseason': 1},
            {'member': '2', 'name': 'Chris Lee', 'role': 'Equipment chief', 'season': 1, 'offseason': 1}]
    out = L.auto_entries(rows, [], 2026, 12)
    assert [(e['member'], e['points'], e['what']) for e in out] == [('1', 1.0, 'Role points, December 2026: Points keeper'),
                                                                  ('2', 1.0, 'Role points, December 2026: Equipment chief')]
    assert L.auto_entries(rows, [{'member': '1', 'what': out[0]['what']}], 2026, 12) == [out[1]]
    assert L.auto_entries(rows, [], 2026, 5)[0]['points'] == 2.0


def test_worker_point_form_rows():
    rows, problems = L.rows_from_form(['1 Jane Smith', 'Bob Jones', '', '2 Pat Quinn'], ['', '3', '', '-1'], MEM, '#AX1',
                                      date(2026, 9, 12), 'Registration', default_points=2)
    assert [(r['member'], r['name'], r['points'], r['what'], r['kind']) for r in rows] == [
        ('1', 'Jane Smith', 2, 'Registration', 'Earned'), ('', 'Bob Jones', 3.0, 'Registration', 'Earned')]
    assert problems and 'Use points' in problems[0]
    rows, _ = L.rows_from_form(['1'], ['12'], MEM, '', date(2026, 12, 1), L.VOLUNTEER_JOB)
    assert rows[0]['kind'] == 'Role points'


def test_every_members_template_view_compiles():
    import os
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), '..', 'templates')))
    env.get_template('members.html')   # a syntax error here is a 500 on every Members page


def test_lapsed_then_rejoined_is_tracked():
    rows = [('Member ID', 'First Name', 'Last Name', 'SCCA Member Expire', 'Region Join Date'), ('500100_0', 'Dana', 'Byers', '07/31/2027', '01/01/2001')]
    m = {'member': '500100', 'first': 'Dana', 'last': 'Byers', 'life': False, 'points': 5.0}
    first = L.merge_roster([m], {}, date(2026, 11, 2))[0]                 # not on the list: lapsed
    assert first['lapsed'] == date(2026, 11, 2) and first['rejoined'] is None
    back = L.merge_roster([first], L.roster(rows), date(2026, 12, 1))[0]   # back on a later list: rejoined
    assert back['lapsed'] is None and back['rejoined'] == date(2026, 12, 1)
    exp = L.merge_roster([back], L.roster([rows[0], ('500100_0', 'Dana', 'Byers', '11/30/2026', '01/01/2001')]), date(2027, 1, 5))[0]
    assert exp['lapsed'] == date(2026, 11, 30) and exp['rejoined'] == date(2026, 12, 1)   # expired on the list: lapsed again


def test_unreconciled_skips_opening_pending_and_booked():
    e = [{'status': 'Approved', 'kind': 'Opening', 'reconciled': '', 'points': 5},
         {'status': 'Approved', 'kind': 'Earned', 'reconciled': '2026-11-02', 'points': 4},
         {'status': 'Pending', 'kind': 'Earned', 'reconciled': '', 'points': 2},
         {'status': 'Approved', 'kind': 'Role points', 'reconciled': '', 'points': 1}]
    assert [x['points'] for x in L.unreconciled(e)] == [1]


def test_negative_adjustment_releases_reserve():
    b = L.books([{'status': 'Approved', 'kind': 'Adjustment', 'points': -2.0, 'event': ''}], {})
    assert b['lines'][0]['debit'] == 'Reserve Fund - Worker Awards' and b['lines'][0]['amount'] == 10.0


def test_every_members_page_renders():
    """Every Members tab and link resolves (a missing route is a 500 on every page)."""
    import os
    os.environ.setdefault('STORAGE_BACKEND', 'files')
    from flask import Flask, render_template
    import members
    app = Flask('t', template_folder=os.path.join(os.path.dirname(__file__), '..', 'templates'))
    app.secret_key = 'x'
    app.register_blueprint(members.bp)
    for ep in ('home', 'logout', 'audit'):
        app.add_url_rule('/' + ep, ep, lambda: '')
    from datetime import date
    base = dict(title='t', keeper=True, point_value=5, today_=date(2026, 11, 2), structure=[], members=[], events=[], form={}, grid=[])
    views = [dict(view='home', total=0, reserve=None, open_value=0, pending=[], lapsed=[], latest_list=None, list_url='', sheet_url='', scca_reports=''),
             dict(view='enter'), dict(view='use', uses=['Other']), dict(view='auto', auto=[]),
             dict(view='quickbooks', open_entries=[], jes=[], used=[], total=0, reserve=None, diff=None, open_value=0, unexplained=None),
             dict(view='milestones', month='2026-11', proposed={}, published={}, differs=False, latest_list=None, years=[0, 5], public_url='')]
    with app.test_request_context('/members/'):
        for v in views:
            assert render_template('members.html', **dict(base, **v))


def test_one_journal_entry_per_event():
    ev = {'#A1': {'program_code': 'AX', 'event_name': 'Solo 1'}, '#R1': {'program_code': 'RX', 'event_name': 'RX 1'}}
    e = [{'status': 'Approved', 'kind': 'Earned', 'event': '#A1', 'points': 4, 'date': date(2026, 11, 3)},
         {'status': 'Approved', 'kind': 'Earned', 'event': '#A1', 'points': 2, 'date': date(2026, 11, 3)},
         {'status': 'Approved', 'kind': 'Earned', 'event': '#R1', 'points': 3, 'date': date(2026, 11, 9)},
         {'status': 'Approved', 'kind': 'Role points', 'event': '', 'points': 5, 'date': date(2026, 11, 1)},
         {'status': 'Approved', 'kind': 'Forfeit', 'event': '', 'points': -7, 'date': date(2026, 11, 20)},
         {'status': 'Approved', 'kind': 'Used', 'event': '', 'points': -10, 'date': date(2026, 11, 21), 'name': 'P', 'what': 'x'}]
    jes, used = L.journal_entries(e, ev)
    assert [(j['title'], j['debit'].split(':')[0], j['amount']) for j in jes] == [
        ('#A1 Solo 1', 'Club AX Autocross Expense', 30.0), ('#R1 RX 1', 'Club RX Rallycross Expense', 15.0),
        ('Forfeits and write-offs', 'Reserve Fund - Worker Awards', 35.0), ('Role points and other points', 'Operations Expense', 25.0)]
    assert len(used) == 1
    subject, body = L.books_email({'jes': jes, 'used': used}, 'November 2026', 100, 100)
    assert 'Journal entry 1: #A1 Solo 1' in body and 'Debit   Club AX Autocross Expense:Club AX Autocross Worker Points   $30.00' in body
