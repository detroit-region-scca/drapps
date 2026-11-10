"""The replacement plan in two parts, a page each: the plan year's purchases (not from the reserve fund, paid from that
year's Operations budget) and the Reserve fund (everything due later: what is in it, what to put in each month through
the year each item is due). No rule to change, and nothing about a board amount on any page. Made-up names only."""
# ruff: noqa: F811  (the ops fixture is imported from test_ops_flow, then named as an argument)
import html as html_lib
import re
from datetime import date

import pytest

import equip_store as E
from test_ops_flow import O, T, Tab, _flashes, _item, _main, _page, _text, ops  # noqa: F401  (ops is the fixture)

REPL = f'{O}/money/replacement'      # the old address: the Annual Plan still links to it
PUR, RES = f'{O}/money/purchases', f'{O}/money/reserve'
BOARD = {'monthly': 500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}


# The reserve balance -------------------------------------------------------------------------------------------------

def _sheet(*extra):
    return [{'depth': 0, 'name': 'ASSETS', 'amount': 90000.0, 'as_of': '2026-11-08'},
            {'depth': 1, 'name': 'Equity', 'amount': 90000.0, 'as_of': '2026-11-08'},
            {'depth': 2, 'name': 'Retained Earnings', 'amount': 70000.0, 'as_of': '2026-11-08'}] + \
        [{'depth': 2, 'name': n, 'amount': a, 'as_of': '2026-11-08'} for n, a in extra]


def test_the_reserve_balance_is_read_from_the_stored_balance_sheet():
    assert E.reserve_balance(_sheet(('Equipment Replacement Reserve', 12000.0))) == {'amount': 12000.0, 'found': True, 'as_of': '2026-11-08'}
    assert E.reserve_balance(_sheet((' equipment  replacement reserve ', 250.5)))['amount'] == 250.5       # however it was typed
    # QuickBooks leaves an account with no balance off the report: a zero row and no row both mean nothing is booked
    assert E.reserve_balance(_sheet(('Equipment Replacement Reserve', 0.0))) == {'amount': 0.0, 'found': False, 'as_of': '2026-11-08'}
    assert E.reserve_balance(_sheet()) == {'amount': 0.0, 'found': False, 'as_of': '2026-11-08'}
    assert E.reserve_balance(_sheet(('Equipment Replacement Reserve Fund', 5.0)))['found'] is False        # another account
    assert E.reserve_balance([]) == {'amount': 0.0, 'found': False, 'as_of': ''}                           # before any sync


def test_a_balance_sheet_that_cannot_be_read_is_a_reserve_of_nothing(monkeypatch):
    import plan_store as P
    monkeypatch.setattr(P, 'enabled', lambda: True)
    monkeypatch.setattr(P, 'balance_sheet', lambda: 1 / 0)
    assert E.reserve_now() == {'amount': 0.0, 'found': False, 'as_of': ''}
    monkeypatch.setattr(P, 'balance_sheet', lambda: _sheet(('Equipment Replacement Reserve', 3000.0)))
    assert E.reserve_now()['amount'] == 3000.0
    monkeypatch.setattr(P, 'enabled', lambda: False)
    assert E.reserve_now()['found'] is False


# The 'Plan settings' tab: the old rows stay, unread and unharmed ---------------------------------------------------------

def test_the_boards_amount_is_three_more_rows_and_touches_nothing_else(monkeypatch, tmp_path):
    monkeypatch.setattr(E, 'LOCK', str(tmp_path / 'equipment.lock'))
    monkeypatch.setattr(E, 'refresh', lambda: None)
    tab = Tab([E.PLAN_LABELS, ['budget_within_years', 3, 'help', '2026-01-01', 'Pat Example'], ['note', 'Radios first.', 'help', '2026-02-02', 'Ed Gear'],
               ['big_cost', 25000], ['big_from_savings', 'no'], ['typed by hand', 'x']])
    monkeypatch.setattr(E, '_plan_ws', lambda: tab)
    before = [list(r) for r in tab.rows[1:]]
    assert E._board_amount(tab.rows[1:]) is None                                    # absent = none recorded
    E.save_board_amount(500.0, '2026-11-05', 'Kim Officer', '2026-11-09')
    assert E._board_amount(tab.rows[1:]) == {'monthly': 500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}
    rows = {r[0]: r for r in tab.rows[1:]}
    assert rows['board_monthly'][1] == 500.0 and rows['board_monthly'][3:] == ['2026-11-09', 'Kim Officer']
    assert [r[:len(b)] for b in before for r in tab.rows if r[0] == b[0]] == before      # every other row exactly as it was
    assert E._note_text(tab.rows[1:]) == 'Radios first.'
    E.save_board_amount(650.0, '2027-01-12', 'Kim Officer', '2027-01-13')
    assert [r[0] for r in tab.rows].count('board_monthly') == 1 and E._board_amount(tab.rows[1:])['monthly'] == 650.0
    E.save_plan_note('', 'Ed Gear', '2027-02-02')
    assert E._board_amount(tab.rows[1:]) == {'monthly': 650.0, 'date': '2027-01-12', 'by': 'Kim Officer'}
    assert [r[:2] for r in tab.rows if r[0] == 'budget_within_years'] == [['budget_within_years', 3]]      # the old rule row is still there
    # typed into the sheet by hand: "$1,200", a date as a day number; a blank or unreadable amount is "none recorded"
    assert E._board_amount([['board_monthly', '$1,200'], ['board_date', 46331]]) == {'monthly': 1200.0, 'date': '2026-11-05', 'by': ''}
    assert E._board_amount([['board_monthly', ''], ['board_date', '2026-11-05']]) is None
    assert E._board_amount([['board_monthly', 'lots']]) is None


def test_old_rule_rows_in_the_sheet_change_nothing(monkeypatch):
    items = [{'id': f'EQ-{n}', 'name': n, 'status': 'In use', 'cost': c, 'year': '', 'life_years': '', 'replace_year': y, 'replace_cost': c}
             for n, y, c in (('Now', 2027, 6000.0), ('Next', 2028, 12000.0), ('Later', 2030, 24000.0))]
    t = date(2026, 11, 15)
    plain = E.reserve_plan(items, t)
    for rule in (0, 1, 2, 5):
        monkeypatch.setattr(E, '_plan_rows', lambda rule=rule: [['budget_within_years', rule], ['big_cost', 25000], ['big_within_years', 3],
                                                                ['big_from_savings', 'yes'], ['board_monthly', 500], ['board_date', '2026-11-05']])
        assert E.plan_settings() == {} and E.board_amount()['monthly'] == 500.0        # the board rows still read; the rule rows do not
        rp = E.reserve_plan(items, t)
        assert rp == plain and [r['it']['name'] for r in rp['near']] == ['Now'] and [r['it']['name'] for r in rp['later']] == ['Next', 'Later']
        assert [what for what, _ in E.budget(2028, items, [], [], t)['Capital Equipment (no account set)']['items']] == ['Next']


def test_the_old_board_address_still_works_for_officers_only(ops):
    ed, lou, state = ops
    kim = state['client']('kim@example.org')
    state['note'] = 'Radios first.'
    for c in (ed, lou):                                                   # Ed edits Operations, but this is a board decision
        r = c.post(f'{O}/plan/board', data={'board_monthly': '500', 'board_date': '2026-11-05'})
        assert r.status_code == 302 and state['board'] is None and not state['audit'] and 'roles the admin allows' in _flashes(c)
    r = kim.post(f'{O}/plan/board', data={'board_monthly': '$1,500', 'board_date': '2026-11-05'})
    assert r.headers['Location'] == RES and state['board'] == {'monthly': 1500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}
    assert state['audit'] == [('EQUIP_PLAN_BOARD', {'before': {}, 'after': {'monthly': 1500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}})]
    assert state['note'] == 'Radios first.' and not state['saves']       # nothing else is touched
    kim.post(f'{O}/plan/board', data={'board_monthly': '1500', 'board_date': '2026-11-05'})
    assert len(state['audit']) == 1 and 'Nothing changed.' in _flashes(kim)
    for bad in ({'board_monthly': 'some', 'board_date': '2026-11-05'}, {'board_monthly': '-5', 'board_date': '2026-11-05'},
                {'board_monthly': '900', 'board_date': ''}, {'board_monthly': '900', 'board_date': '2999-01-01'}, {}):
        kim.post(f'{O}/plan/board', data=bad)
        assert state['board']['monthly'] == 1500.0 and len(state['audit']) == 1 and 'Nothing was saved' in _flashes(kim), bad
    for c, url in ((c, u) for c in (kim, ed, lou) for u in (PUR, RES)):   # and no page links to it or shows what it holds
        page = _page(c, url)
        assert 'plan/board' not in page and 'rule-board' not in page and '1,500' not in page


def test_the_audit_log_says_it_in_a_sentence():
    import app as A
    import audit_view
    with A.app.test_request_context('/'):
        text = audit_view.plain({'action': 'EQUIP_PLAN_BOARD', 'user': 'Kim Officer', 'details': {'before': {}, 'after': {'monthly': 1500.0, 'date': '2026-11-05'}}})
    assert text == 'Kim Officer recorded what the board puts in the reserve fund: $1,500 a month'
    assert audit_view.app_of('EQUIP_PLAN_BOARD') == audit_view.app_of('EQUIP_PLAN_NOTE')


# The two pages, for each of the three people ----------------------------------------------------------------------

@pytest.fixture()
def plan(ops):
    """A realistic plan: purchases due this plan year, the next, in 3 and 4 years at $40,000 each, and far out."""
    ed, lou, state = ops
    y = E.reserve_plan([])['year']
    state['y'] = y
    state['items'] = [
        _item('EQ-001', 'Blue tow truck', 'Vehicle', replace_year=float(y), replace_cost=45000.0, updated='2026-10-01', updated_by='Ed Gear'),
        _item('EQ-002', 'New radios', 'Safety', status='Planned', place='', replace_year=float(y), replace_cost=6000.0),
        _item('EQ-003', 'Timing trailer', 'Trailer', replace_year=y + 1.0, replace_cost=12000.0),
        _item('EQ-004', 'Long trailer', 'Trailer', replace_year=y + 3.0, replace_cost=40000.0, updated='2026-11-03', updated_by='Pat Example'),
        _item('EQ-005', 'Motorhome', 'Vehicle', replace_year=y + 4.0, replace_cost=40000.0),
        _item('EQ-006', 'Pop-up tent', 'Hospitality', replace_year=y + 14.0, replace_cost=5000.0)]
    return state['client']('kim@example.org'), ed, lou, state


def test_the_page_opens_with_one_plain_sentence(plan):
    kim, ed, lou, state = plan
    for c, url in ((c, u) for c in (kim, ed, lou) for u in (PUR, RES)):
        text = _text(_main(_page(c, url)))
        assert 'The board approves' in text and 'Change it any time' not in text
        for gone in ('living estimate', 'whenever you learn', 'Last changed', 'Pat Example', 'Big items', 'big item', 'from savings', 'lump', 'motion',
                     'propose it', 'for the rest'):
            assert gone not in text, gone
    assert not hasattr(E, 'plan_changed')


def test_this_years_purchases_then_the_reserve_fund_by_whole_years(plan):
    kim, ed, lou, state = plan
    y = state['y']
    rp = E.reserve_plan(state['items'])
    assert [r['year'] for r in rp['near']] == [y, y] and [r['year'] for r in rp['later']] == [y + 1, y + 3, y + 4, y + 14]
    m = f"${rp['monthly']:,.0f}"
    pur = _main(_page(lou, PUR))
    text = _text(pur)
    assert f'>Budgeted purchases for {y}</h1>' in pur and '>Reserve fund</h1>' not in pur and 'id="schedule"' not in pur
    assert f'${51000:,} planned for {y}. Paid from the {y} Operations budget, not the reserve fund.' in text
    assert f'href="/plan/{y}/OPS">{y} Operations budget (officers edit it)</a>' in pur
    near = pur.split(f'aria-label="Budgeted purchases for {y}"')[1].split('</table>')[0]
    assert 'Blue tow truck' in near and 'New radios' in near and 'Timing trailer' not in pur        # next year's item is not here
    assert '>Year</th>' not in near and 'Total$51,000' in _text(near)               # all the plan year: no Year column for a reader
    html = _main(_page(lou, RES))
    text = _text(html)
    assert '>Reserve fund</h1>' in html and 'Blue tow truck' not in html
    assert f'For everything due after {y}, saved for month by month through the year each item is due.' in text
    assert f'In it now: $0, nothing booked yet Put in {m} a month in {y}' in text       # before the first sync that has it
    # the schedule is a table: one row per whole year the amount changes, with what is paid for that year
    sched = html.split('id="schedule"')[1].split('</table>')[0]
    assert f'What to put in each month, from {rp["start_label"]}' in text and 'class="table mb-0 stack"' in html
    assert [_text(c) for c in re.findall(r'<th[^>]*>(.*?)</th>', sched)] == ['Through', 'A month', 'Paid for that year']
    rows = [[_text(c) for c in re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)] for r in sched.split('<tbody>')[1].split('</tr>')[:-1]]
    paid = ('Timing trailer $12,000', 'Long trailer $40,000', 'Motorhome $40,000', 'Pop-up tent $5,000')
    assert len(rp['steps']) == 4 and rows == [[str(yr), f'${mo:,.0f}', what] for (yr, mo), what in zip(rp['steps'], paid)]
    assert [r[0] for r in rows] == [str(y + 1), str(y + 3), str(y + 4), str(y + 14)]
    assert rows[0][1] == m and sched.count('data-label="A month"') == sched.count('data-label="Paid for that year"') == 4
    # every reserve fund row shows its own monthly amount; next year's item is saved for like the rest
    later = html.split('aria-label="Reserve fund items"')[1]
    assert 'Timing trailer' in later and all(f"${r['monthly']:,.0f}" in later for r in rp['later']) and f'$97,000{m}' in _text(later.split('>Total<')[1])
    assert '>A month</th>' in later

    state['board'] = dict(BOARD)                                                        # an amount left in the sheet changes nothing
    state['sheet'] = _sheet(('Equipment Replacement Reserve', 12000.0))
    html = _main(_page(lou, RES))
    text = _text(html)
    assert 'In it now: $12,000 (QuickBooks account Equipment Replacement Reserve, as of Nov 8, 2026)' in text and 'nothing booked yet' not in text
    assert text.count('Equipment Replacement Reserve') == 1
    m2 = f"${E.reserve_plan(state['items'], balance=12000.0)['monthly']:,.0f}"       # less: the reserve fund counts toward what is due first
    assert m2 != m and f'Put in {m2} a month in {y}' in text and f'{y + 3} {m2} Timing trailer $12,000 ({y + 1}), Long trailer $40,000' in _text(html.split('id="schedule"')[1])
    assert 'The $12,000 in the reserve fund counts toward the items due first.' in text
    state['sheet'] = _sheet(('Equipment Replacement Reserve', 0.0))
    assert 'In it now: $0, nothing booked yet' in _text(_main(_page(lou, RES)))
    state['sheet'] = _sheet(('Equipment Replacement Reserve', 200000.0))                # more than everything due later
    html = _main(_page(lou, RES))
    assert 'Nothing more to put in: the reserve fund already covers everything due later.' in _text(html) and 'id="schedule"' not in html
    state['items'] = state['items'][:2]                                            # nothing left to save for
    html = _main(_page(lou, RES))
    assert 'Nothing is due later' in _text(html) and 'id="schedule"' not in html and 'aria-label="Reserve fund items"' not in html


def test_nothing_due_in_the_plan_year_is_one_line_and_no_table(plan):
    kim, ed, lou, state = plan
    y = state['y']
    state['items'] = state['items'][2:]
    for c in (kim, ed, lou):
        html = _main(_page(c, PUR))
        text = _text(html)
        assert f'No planned purchases for {y}.' in text and f'aria-label="Budgeted purchases for {y}"' not in html and 'planned for' not in text
        assert f'{y} Operations budget (officers edit it)' in text and 'Timing trailer' not in html
        assert ('Add a planned purchase' in html) == (c is not lou)                   # the committee can still add one
        assert 'Timing trailer' in _main(_page(c, RES)).split('aria-label="Reserve fund items"')[1]
    assert _card(lou).startswith(f'$0 of purchases planned for {y} Reserve fund: ')


def test_the_schedule_says_what_each_year_pays_for():
    items = [{'id': f'EQ-{n}', 'name': n, 'status': 'In use', 'cost': c, 'year': '', 'life_years': '', 'replace_year': y, 'replace_cost': c}
             for n, y, c in (('First', 2029, 12000.0), ('Second', 2030, 24000.0), ('Also second', 2030, 4200.0))]
    t = date(2027, 1, 15)
    rp = E.reserve_plan(items, t)
    assert [(s['year'], s['monthly']) for s in rp['schedule']] == rp['steps'] and [s['year'] for s in rp['schedule']] == [2029, 2030]
    assert [E.paid_for_text(s) for s in rp['schedule']] == ['First $12,000', 'Second $24,000, Also second $4,200']
    # the reserve fund pays for First, so its year is no step: it sits in the next one, with its own year
    rp = E.reserve_plan(items, t, balance=12000.0)
    assert [s['year'] for s in rp['schedule']] == [2030] and E.paid_for_text(rp['schedule'][0]) == 'First $12,000 (2029), Second $24,000, Also second $4,200'
    assert sum(len(s['items']) for s in rp['schedule']) == len(rp['later'])
    assert E.reserve_plan(items, t, balance=99999.0)['schedule'] == []


def test_the_reserve_fund_counts_toward_what_is_due_first_and_the_amount_steps_down():
    items = [{'id': f'EQ-{n}', 'name': n, 'status': 'In use', 'cost': c, 'year': '', 'life_years': '', 'replace_year': y, 'replace_cost': c}
             for n, y, c in (('First', 2029, 12000.0), ('Second', 2030, 24000.0))]
    t = date(2027, 1, 15)
    rp = E.reserve_plan(items, t)
    assert [r['monthly'] for r in rp['later']] == [round(12000 / 36, 2), 500.0] and rp['saved'] == 0      # 36 and 48 months, through December
    assert rp['steps'] == [(2029, round(12000 / 36 + 500, 2)), (2030, 500.0)]
    rp = E.reserve_plan(items, t, balance=15000.0)                                                   # pays First, and 3,000 of Second
    assert [r['saved'] for r in rp['later']] == [12000.0, 3000.0] and rp['monthly'] == 437.5 and rp['steps'] == [(2030, 437.5)]
    rp = E.reserve_plan(items, date(2027, 6, 2))                                                     # from June: 31 and 43 months
    assert rp['start'] == '2027-06' and [r['monthly'] for r in rp['later']] == [round(12000 / 31, 2), round(24000 / 43, 2)]


def test_what_each_of_the_three_people_can_do(plan):
    kim, ed, lou, state = plan
    fields = lambda html: set(re.findall(r'<(?:input|select|textarea)[^>]* name="(\w+)"', html)) - {'_csrf', 'action', 'back'}  # noqa: E731
    for board in (None, dict(BOARD)):
        state['board'] = board
        assert fields(_main(_page(kim, PUR))) == fields(_main(_page(ed, PUR))) == {'note', 'replace_year', 'replace_cost'}
        assert 'Entries for QuickBooks' not in _page(kim, PUR) and not re.search(r'<(form|input|button|select|textarea|details)\b', _main(_page(lou, PUR)))
        officer, editor, looker = (_main(_page(c, RES)) for c in (kim, ed, lou))
        # the officer: every editor control, plus the entries for QuickBooks
        assert fields(officer) == fields(editor) == {'note', 'replace_year', 'replace_cost'}
        assert '<summary>Entries for QuickBooks</summary>' in officer and '<details class="small mt-3" id="entries">' in officer      # collapsed
        assert 'Entries for QuickBooks' not in editor and 'Take off the plan' in editor and 'Take off the plan' in officer
        # everyone else: plain text
        assert not re.search(r'<(form|input|button|select|textarea|details)\b', looker)
        for html in (officer, editor, looker):
            assert 'rule-board' not in html and 'rule-soon' not in html and 'id="board"' not in html and 'id="projection"' not in html
        for c, url in ((c, u) for c in (kim, ed, lou) for u in (PUR, RES)):
            html = _page(c, url)
            ids = re.findall(r'\sid="([^"]+)"', html)
            assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]


def test_the_entries_for_quickbooks_are_shown_not_made(plan):
    kim, ed, lou, state = plan
    y = state['y']
    box = lambda: _text(_main(_page(kim, RES)).split('<details')[1].split('</details>')[0])  # noqa: E731
    text = box()
    mo = E.reserve_plan(state['items'])['monthly']
    assert 'The treasurer makes these two journal entries by hand. The app does not write them to QuickBooks.' in text
    assert (f'Once a year, when the budget is approved. Twelve months at ${mo:,.0f} (what the schedule calls for in {y}): '
            f'debit Retained Earnings ${mo * 12:,.0f}, credit Equipment Replacement Reserve ${mo * 12:,.0f}.') in text
    assert 'When a reserve fund item is bought. Take what was saved for it back out: debit Equipment Replacement Reserve, credit Retained Earnings' in text
    assert 'Blue tow truck' not in text and 'Purchases not from the reserve fund need no entry.' in text and 'Operations budget' not in text
    state['board'] = dict(BOARD)                                           # an amount left in the sheet is not used
    assert box() == text and 'board' not in text.lower()
    state['sheet'] = _sheet(('Equipment Replacement Reserve', 200000.0))   # the reserve fund covers it all: nothing to enter this year
    assert f'Once a year, when the budget is approved. Nothing to put in for {y}, so no entry.' in box()


# The Overview card and the report --------------------------------------------------------------------------------------

def _card(c):
    return _text(_main(_page(c, f'{O}/')).split('<div class="label">Replacements</div>')[1].split('</a></div></div>')[0])


def test_the_overview_card_says_what_is_true_now(plan):
    kim, ed, lou, state = plan
    y = state['y']
    m = f"${E.reserve_plan(state['items'])['monthly']:,.0f}"
    assert _card(lou) == f'$51,000 of purchases planned for {y} Reserve fund: {m} a month, $0 in it now'
    state['board'] = dict(BOARD)
    state['sheet'] = _sheet(('Equipment Replacement Reserve', 12000.0))
    m2 = f"${E.reserve_plan(state['items'], balance=12000.0)['monthly']:,.0f}"
    for c in (kim, ed, lou):                                              # always what the plan calls for, whatever the sheet holds
        assert _card(c) == f'$51,000 of purchases planned for {y} Reserve fund: {m2} a month, $12,000 in it now'
    home = _page(lou, f'{O}/')
    assert 'from savings' not in home and 'big item' not in home and f'href="{PUR}"><div class="label">Replacements</div>' in home
    assert f'<a href="{RES}">Reserve fund: ' in home


def test_the_report_says_the_same(plan):
    import io

    import equip_pdf
    PdfReader = pytest.importorskip('pypdf').PdfReader      # only on the maintainer's computer; the server image has no PDF reader
    kim, ed, lou, state = plan
    y = state['y']

    def words(**kw):
        pdf = equip_pdf.build(state['items'], [], [], today=T, **kw)
        return ' '.join(' '.join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf)).pages).split())
    state['board'] = dict(BOARD)
    for text in (words(), words(reserve=E.reserve_balance(_sheet(('Equipment Replacement Reserve', 12000.0))))):
        assert f'{y} purchases (not from the reserve fund)' in text and f'of purchases planned for {y}' in text
        assert f'Planned purchases for {y}: $51,000. Paid from the {y} Operations budget, not the reserve fund.' in text
        assert f'For everything due after {y}. Saved for month by month through the year each item is due.' in text
        assert 'Through A month Paid for that year' in text and 'What we expect to replace or buy, and when.' in text
        assert 'from savings' not in text and 'big items' not in text and '*' not in text
        for gone in RETIRED + ('board',):
            assert gone not in text.lower(), gone
    assert 'In the reserve fund now: $0, nothing booked yet.' in words()
    assert 'In the reserve fund now: $12,000 (QuickBooks account Equipment Replacement Reserve, as of Nov 8, 2026).' in text
    assert 'The $12,000 in the reserve fund counts toward the items due first.' in text
    state['items'] = state['items'][2:]
    assert f'No planned purchases for {y}.' in words()


# The Operations budget page ------------------------------------------------------------------------------------------

@pytest.fixture()
def budget(plan, monkeypatch):
    """The Operations budget for the plan year: one bill account that matches the records, one that no longer does,
    and the equipment bought that year."""
    import plan as plan_mod
    kim, ed, lou, state = plan
    y = str(state['y'])
    state['tasks'] = [dict(t, account='Operations Professional Services') if t['id'] == 'TK-004' else t for t in state['tasks']]
    state['items'][0]['account'] = 'Capital Equipment:Race Equipment'
    state['items'][1]['account'] = 'Capital Equipment:Race Equipment'
    lines = [{'year': y, 'program': 'OPS', 'item': 'Web hosting', 'month': '', 'net': -336.0, 'event_code': '', 'note': '', 'sort': 0, 'account': 'Operations Professional Services'},
             {'year': y, 'program': 'OPS', 'item': 'Storage', 'month': '', 'net': -2400.0, 'event_code': '', 'note': '', 'sort': 1, 'account': 'Operations Storage'},
             {'year': y, 'program': 'OPS', 'item': 'Equipment', 'month': '', 'net': -51000.0, 'event_code': '', 'note': '', 'sort': 2, 'account': 'Capital Equipment:Race Equipment'}]
    info = {'updated': '2026-11-01', 'updated_by': 'Ed Gear', 'submitted': '', 'approved': '', 'notes': ''}
    state['plan'] = {'OPS': {'lines': lines, 'net': -53736.0, 'savings': 0.0, 'info': info}}
    P = plan_mod.P
    monkeypatch.setattr(plan_mod, '_guard', lambda: None)
    monkeypatch.setattr(plan_mod, 'load_programs', lambda: {'AX': 'Autocross'})
    monkeypatch.setattr(plan_mod, 'load_events', lambda: [])
    monkeypatch.setattr(plan_mod.O, 'all', lambda: [])
    monkeypatch.setattr(plan_mod.O, 'director', lambda p: ('', ''))
    monkeypatch.setattr(plan_mod.B, 'enabled', lambda: False)
    monkeypatch.setattr(P, 'plan', lambda yr: {k: dict(v, lines=[dict(ln) for ln in v['lines']]) for k, v in state['plan'].items()} if str(yr) == y else {})
    monkeypatch.setattr(P, 'actuals', lambda yr: ({}, ''))
    monkeypatch.setattr(P, 'exceptions', lambda include_reviewed=False: [])
    state['url'] = f'/plan/{y}/OPS'
    return kim, ed, lou, state


def test_the_budget_page_says_what_the_plan_shows_quietly(budget):
    kim, ed, lou, state = budget
    for c in (kim, ed, lou):
        html = _main(_page(c, state['url']))
        text = _text(html)
        # storage: the budget says $2,400, the records $3,000 (North barn, $250 a month): one grey line, no warning
        assert html.count('The replacement plan now shows') == 1 and 'The replacement plan now shows $3,000 for this year.' in text
        line = html.split('The replacement plan now shows')[0].rsplit('<tr', 1)[1] + html.split('The replacement plan now shows')[1].split('</tr>')[0]
        assert 'text-muted' in line and 'text-warning' not in line and 'text-danger' not in line
        for gone in ('add up to', 'edit the budget to match', 'turns amber', 'motion', 'stays in savings'):
            assert gone not in text, gone
        assert 'Changed since approved' not in text and 'Draft' in text               # the status is the plan's own
        assert 'Blue tow truck $45,000' in text and 'New radios $6,000' in text       # the equipment bought that year
        assert 'Timing trailer' not in text                                           # next year's is in next year's budget
    # a difference under a dollar says nothing
    state['plan']['OPS']['lines'][1]['net'] = -3000.4
    assert 'plan now shows' not in _page(lou, state['url'])


def test_the_budget_page_shows_the_reserve_fund_under_the_total(budget):
    kim, ed, lou, state = budget
    rp = E.reserve_plan(state['items'])
    row = lambda c: _text(_main(_page(c, state['url'])).split('id="set-aside"')[1].split('</div>')[0].split('>', 1)[1])  # noqa: E731
    line = f"Reserve fund: the plan calls for ${rp['monthly']:,.0f} a month (${rp['monthly'] * 12:,.0f} for the year). Not in the total."
    assert row(lou) == line
    state['board'] = dict(BOARD)                                                     # an amount left in the sheet changes nothing
    for c in (kim, ed, lou):
        assert row(c) == line
    html = _main(_page(lou, state['url']))
    assert html.index('class="total-row"') < html.index('id="set-aside"')
    total = _text(html.split('class="total-row"')[1].split('</tr>')[0])
    assert '-$53,736' in total and f"{rp['monthly'] * 12:,.0f}" not in total         # the reserve fund does not add to the total
    assert f'href="{RES}">Reserve fund</a>' in html


RETIRED = ('buy soon', 'save for later', 'set aside', 'set-aside', 'suggest', 'has not set an amount', 'july', 'not picked', 'living estimate',
           'last changed', 'due by', 'the board puts in', 'record the board', 'that covers everything', 'falls short', 'operations budget items')


def test_the_retired_words_are_gone_for_all_three_people(budget):
    """Two things to read: "Reserve fund" and purchases "not from the reserve fund". What they used to be called, the
    month of July, the old rule and anything about a board amount are on no page a person reads, whatever old rows the
    sheet holds, whatever is in the reserve fund and whether or not anything is due in the plan year."""
    kim, ed, lou, state = budget
    all_items = list(state['items'])

    def read(html):      # what a person reads or hears: the text, and the labels on controls
        return (html_lib.unescape(_text(html)) + ' ' + ' '.join(re.findall(r'(?:aria-label|title|placeholder|data-label)="([^"]*)"', html))).lower()
    for items in (all_items, all_items[2:], all_items[:2]):                  # this year and later, nothing this year, nothing later
        for board in (None, dict(BOARD)):
            for sheet in (_sheet(), _sheet(('Equipment Replacement Reserve', 45000.0))):
                state['items'], state['board'], state['sheet'] = items, board, sheet
                for c in (kim, ed, lou):
                    for url in (PUR, RES, f'{O}/', state['url']):
                        text = read(_main(_page(c, url)))
                        for gone in RETIRED:
                            assert gone not in text, (gone, url)
                        assert not re.search(r'the reserve(?! fund)', text), url
                    pur, res = _main(_page(c, PUR)), _main(_page(c, RES))
                    assert f"budgeted purchases for {state['y']}" in read(pur) and '>Operations budget</h2>' not in pur
                    # nothing about the board: either page, the Overview card, the budget page's reserve line
                    assert all(w not in read(p) for p in (res, pur) for w in ('board puts in', 'record the board', 'has not picked', 'board set aside'))      # only that the board approves
                    assert 'board' not in _card(c).lower()
                    assert 'board' not in read(_main(_page(c, state['url'])).split('id="set-aside"')[1].split('</div>')[0])
    # the old rule's address says nothing and saves nothing
    kim.post(f'{O}/plan/settings', data={'budget_within_years': '1'})
    assert _flashes(kim) == '' and not state['audit'] and not state['saves']
