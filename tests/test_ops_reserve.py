"""The replacement plan as a living estimate with a board-set reserve: three numbers kept apart (what the plan
suggests, what the board set aside, what QuickBooks has in the reserve), one projection sentence, the board's amount
recorded only by officers, and no "big items from savings" anywhere. Made-up names only."""
# ruff: noqa: F811  (the ops fixture is imported from test_ops_flow, then named as an argument)
import re
from datetime import date

import pytest

import equip_store as E
from test_ops_flow import O, T, Tab, _flashes, _item, _main, _page, _text, ops  # noqa: F401  (ops is the fixture)

REPL = f'{O}/money/replacement'


def _later(*rows):
    """Save-for-later rows as reserve_plan gives them, saving from January 2027: (name, year, cost)."""
    return [{'it': {'name': n}, 'year': y, 'cost': c, 'monthly': round(c / E._months_to_july('2027-01', y), 2)} for n, y, c in rows]


# The projection ------------------------------------------------------------------------------------------------------

def test_the_plans_own_suggestion_always_covers_the_plan():
    later = _later(('Long trailer', 2029, 40000.0), ('Tow truck', 2030, 40000.0), ('Tent', 2040, 5000.0))
    pr = E.reserve_projection(later, '2027-01', sum(r['monthly'] for r in later))
    assert pr['state'] == 'covers' and pr['short_by'] == 0
    assert E.projection_text(dict(pr, monthly=2300.0)) == 'At $2,300 a month the reserve covers everything on the plan.'


def test_the_first_shortfall_is_named():
    later = _later(('Long trailer', 2029, 40000.0), ('Tow truck', 2030, 40000.0))
    # $500 a month from January 2027: 30 months = $15,000 in hand in July 2029, with $2,000 already in the reserve
    pr = E.reserve_projection(later, '2027-01', 500.0, 2000.0)
    assert (pr['state'], pr['item'], pr['year'], pr['short_by']) == ('short', 'Long trailer', 2029, 23000.0)
    assert E.projection_text(pr) == 'At $500 a month the reserve is short by about $23,000 when Long trailer is due in 2029.'
    # enough for the first, not for the second: the second is the first shortfall (42 months, less the $40,000 spent)
    pr = E.reserve_projection(later, '2027-01', 1500.0)
    assert (pr['state'], pr['item'], pr['year'], pr['short_by']) == ('short', 'Tow truck', 2030, 17000.0)
    # a few cents short is rounding, not a shortfall
    assert E.reserve_projection(_later(('Tent', 2029, 10000.0)), '2027-01', 333.33)['state'] == 'covers'
    assert E.reserve_projection(later, '2027-01', 0.0)['state'] == 'short'           # the board may decide $0


def test_no_board_amount_says_what_the_plan_suggests():
    later = _later(('Long trailer', 2029, 40000.0))
    pr = E.reserve_projection(later, '2027-01', None, 5000.0)
    assert pr['state'] == 'none' and pr['suggested'] == 1333.33
    assert E.projection_text(pr) == 'The plan suggests $1,333 a month. The board has not set an amount.'


def test_an_empty_plan_has_nothing_to_save_for():
    for monthly in (None, 500.0):
        pr = E.reserve_projection([], '2027-01', monthly, 100.0)
        assert pr['state'] == 'empty' and E.projection_text(pr) == 'Nothing on the plan to save for.'


def test_a_reserve_already_larger_than_the_plan_covers_it_at_any_amount():
    later = _later(('Long trailer', 2029, 40000.0), ('Tow truck', 2030, 40000.0))
    assert E.reserve_projection(later, '2027-01', 0.0, 80000.0)['state'] == 'covers'
    assert E.reserve_projection(later, '2027-01', 100.0, 90000.0)['state'] == 'covers'
    assert E.reserve_projection(later, '2027-01', 0.0, 79000.0)['item'] == 'Tow truck'


def test_the_projection_uses_the_plan_rows_as_they_are():
    rows = [dict(_item('EQ-1', 'Long trailer'), replace_year=2029.0, replace_cost=40000.0),
            dict(_item('EQ-2', 'New radios', status='Planned'), replace_year=2027.0, replace_cost=6000.0)]
    rp = E.reserve_plan(rows, date(2026, 11, 15))
    pr = E.reserve_projection(rp['later'], rp['start'], 1000.0, 12000.0)        # 12,000 + 30 x 1,000 = 42,000
    assert pr['state'] == 'covers' and pr['suggested'] == rp['monthly'] == 1333.33
    assert E.reserve_projection(rp['later'], rp['start'], 900.0, 12000.0)['short_by'] == 1000.0


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


# The board's amount on the 'Plan settings' tab --------------------------------------------------------------------------

def test_the_boards_amount_is_three_more_rows_and_touches_nothing_else(monkeypatch, tmp_path):
    monkeypatch.setattr(E, 'LOCK', str(tmp_path / 'equipment.lock'))
    monkeypatch.setattr(E, 'refresh', lambda: None)
    tab = Tab([E.PLAN_LABELS, ['budget_within_years', 3, 'help', '2026-01-01', 'Pat Example'], ['note', 'Radios first.', 'help', '2026-02-02', 'Ed Gear'],
               ['big_cost', 25000], ['big_from_savings', 'no'], ['typed by hand', 'x']])
    monkeypatch.setattr(E, '_plan_ws', lambda: tab)
    before = [list(r) for r in tab.rows[1:]]
    assert E._board_amount(tab.rows[1:]) is None                                    # absent = not decided yet
    E.save_board_amount(500.0, '2026-11-05', 'Kim Officer', '2026-11-09')
    assert E._board_amount(tab.rows[1:]) == {'monthly': 500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}
    rows = {r[0]: r for r in tab.rows[1:]}
    assert rows['board_monthly'][1] == 500.0 and rows['board_monthly'][3:] == ['2026-11-09', 'Kim Officer']
    assert [r[:len(b)] for b in before for r in tab.rows if r[0] == b[0]] == before      # every other row exactly as it was
    assert E._plan_cfg(tab.rows[1:]) == {'budget_within_years': 3} and E._note_text(tab.rows[1:]) == 'Radios first.'
    E.save_board_amount(650.0, '2027-01-12', 'Kim Officer', '2027-01-13')
    assert [r[0] for r in tab.rows].count('board_monthly') == 1 and E._board_amount(tab.rows[1:])['monthly'] == 650.0
    E.save_plan_settings({'budget_within_years': 2}, 'Ed Gear', '2027-02-01')
    E.save_plan_note('', 'Ed Gear', '2027-02-02')
    assert E._board_amount(tab.rows[1:]) == {'monthly': 650.0, 'date': '2027-01-12', 'by': 'Kim Officer'}
    # typed into the sheet by hand: "$1,200", a date as a day number; a blank or unreadable amount is "not decided"
    assert E._board_amount([['board_monthly', '$1,200'], ['board_date', 46331]]) == {'monthly': 1200.0, 'date': '2026-11-05', 'by': ''}
    assert E._board_amount([['board_monthly', ''], ['board_date', '2026-11-05']]) is None
    assert E._board_amount([['board_monthly', 'lots']]) is None


def test_only_an_officer_records_the_boards_amount(ops):
    ed, lou, state = ops
    kim = state['client']('kim@example.org')
    state['note'] = 'Radios first.'
    cfg = dict(state['cfg'])
    for c in (ed, lou):                                                   # Ed edits Operations, but this is a board decision
        r = c.post(f'{O}/plan/board', data={'board_monthly': '500', 'board_date': '2026-11-05'})
        assert r.status_code == 302 and state['board'] is None and not state['audit'] and 'roles the admin allows' in _flashes(c)
    r = kim.post(f'{O}/plan/board', data={'board_monthly': '$1,500', 'board_date': '2026-11-05'})
    assert r.headers['Location'] == REPL and state['board'] == {'monthly': 1500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}
    assert state['audit'] == [('EQUIP_PLAN_BOARD', {'before': {}, 'after': {'monthly': 1500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}})]
    assert 'the board set aside $1,500 a month' in _flashes(kim)
    assert state['cfg'] == cfg and state['note'] == 'Radios first.' and not state['saves']       # nothing else is touched
    kim.post(f'{O}/plan/board', data={'board_monthly': '1500', 'board_date': '2026-11-05'})
    assert len(state['audit']) == 1 and 'Nothing changed.' in _flashes(kim)
    for bad in ({'board_monthly': 'some', 'board_date': '2026-11-05'}, {'board_monthly': '-5', 'board_date': '2026-11-05'},
                {'board_monthly': '900', 'board_date': ''}, {'board_monthly': '900', 'board_date': '2999-01-01'}, {}):
        kim.post(f'{O}/plan/board', data=bad)
        assert state['board']['monthly'] == 1500.0 and len(state['audit']) == 1 and 'Nothing was saved' in _flashes(kim), bad
    kim.post(f'{O}/plan/board', data={'board_monthly': '1,750', 'board_date': '2026-11-06'})
    assert state['audit'][-1][1]['before']['monthly'] == 1500.0 and state['board']['monthly'] == 1750.0


def test_the_audit_log_says_it_in_a_sentence():
    import app as A
    import audit_view
    with A.app.test_request_context('/'):
        text = audit_view.plain({'action': 'EQUIP_PLAN_BOARD', 'user': 'Kim Officer', 'details': {'before': {}, 'after': {'monthly': 1500.0, 'date': '2026-11-05'}}})
    assert text == 'Kim Officer recorded what the board set aside for the replacement plan: $1,500 a month'
    assert audit_view.app_of('EQUIP_PLAN_BOARD') == audit_view.app_of('EQUIP_PLAN_NOTE')


# The Replacement page, for each of the three people ----------------------------------------------------------------------

@pytest.fixture()
def plan(ops):
    """A realistic plan: purchases due this plan year, the next, in 3 and 4 years at $40,000 each, and far out."""
    ed, lou, state = ops
    y = E.reserve_plan([], None)['start'][:4]
    y = int(y)
    state['y'] = y
    state['items'] = [
        _item('EQ-001', 'Blue tow truck', 'Vehicle', replace_year=float(y), replace_cost=45000.0, updated='2026-10-01', updated_by='Ed Gear'),
        _item('EQ-002', 'New radios', 'Safety', status='Planned', place='', replace_year=float(y), replace_cost=6000.0),
        _item('EQ-003', 'Timing trailer', 'Trailer', replace_year=y + 1.0, replace_cost=12000.0),
        _item('EQ-004', 'Long trailer', 'Trailer', replace_year=y + 3.0, replace_cost=40000.0, updated='2026-11-03', updated_by='Pat Example'),
        _item('EQ-005', 'Motorhome', 'Vehicle', replace_year=y + 4.0, replace_cost=40000.0),
        _item('EQ-006', 'Pop-up tent', 'Hospitality', replace_year=y + 14.0, replace_cost=5000.0)]
    return state['client']('kim@example.org'), ed, lou, state


def test_the_page_says_it_is_a_living_estimate_and_who_last_changed_it(plan):
    kim, ed, lou, state = plan
    for c in (kim, ed, lou):
        text = _text(_main(_page(c, REPL)))
        assert 'A living estimate, not a commitment: change a year or cost whenever you learn something.' in text
        assert 'Last changed Nov 3, 2026 by Pat Example (Long trailer).' in text
        for gone in ('Big items', 'big item', 'from savings', 'lump', 'motion', 'propose it', 'for the rest'):
            assert gone not in text, gone
    for i in state['items']:
        i.update(updated='', updated_by='')
    assert 'Last changed' not in _page(lou, REPL)


def test_three_numbers_kept_apart_and_one_projection(plan):
    kim, ed, lou, state = plan
    y = state['y']
    rp = E.reserve_plan(state['items'])
    assert [r['year'] for r in rp['near']] == [y, y, y + 1] and [r['year'] for r in rp['later']] == [y + 3, y + 4, y + 14]
    m = f"${rp['monthly']:,.0f}"
    text = _text(_main(_page(lou, REPL)))
    assert f'Buy soon (due within 2 years): $51,000 in {y} and $12,000 in {y + 1}. In that year\'s Operations budget.' in text
    assert f'The plan suggests {m} a month from {rp["start_label"]}, to pay for everything under Save for later.' in text
    assert 'The board set aside nothing yet: it has not decided an amount.' in text
    assert 'In the reserve now $0, nothing booked yet.' in text                    # before the first sync that has it
    assert f'The plan suggests {m} a month. The board has not set an amount.' in text
    # every "save for later" row shows its own monthly amount: none is carved out
    later = _main(_page(lou, REPL)).split('Save for later</h2>')[1]
    assert all(f"${r['monthly']:,.0f}" in later for r in rp['later']) and f'$85,000{m}' in _text(later.split('>Total<')[1])

    state['board'] = {'monthly': 500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}
    state['sheet'] = _sheet(('Equipment Replacement Reserve', 12000.0))
    text = _text(_main(_page(lou, REPL)))
    assert 'The board set aside $500 a month, decided Nov 5, 2026, recorded by Kim Officer.' in text
    assert 'In the reserve now $12,000 (QuickBooks as of Nov 8, 2026).' in text and 'nothing booked yet' not in text
    m2 = f"${E.reserve_plan(state['items'], balance=12000.0)['monthly']:,.0f}"       # less: the reserve counts toward what is due first
    assert m2 != m and f'The plan suggests {m2} a month from' in text and 'beyond the $12,000 already in the reserve' in text
    assert 'It drops as things are bought:' in text and f'until July {y + 3}, then ' in text
    short = E.reserve_projection(rp['later'], rp['start'], 500.0, 12000.0)
    assert short['item'] == 'Long trailer' and f"At $500 a month the reserve is short by about ${short['short_by']:,.0f} when Long trailer is due in {y + 3}." in text
    state['board']['monthly'] = 5000.0
    assert 'At $5,000 a month the reserve covers everything on the plan.' in _text(_main(_page(lou, REPL)))
    state['sheet'] = _sheet(('Equipment Replacement Reserve', 0.0))
    assert 'In the reserve now $0, nothing booked yet (QuickBooks as of Nov 8, 2026).' in _text(_main(_page(lou, REPL)))
    state['items'] = state['items'][:3]                                            # nothing left to save for
    assert 'Nothing on the plan to save for.' in _text(_main(_page(lou, REPL)))


def test_what_each_of_the_three_people_can_do(plan):
    kim, ed, lou, state = plan
    state['board'] = {'monthly': 500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}
    fields = lambda html: set(re.findall(r'<(?:input|select|textarea)[^>]* name="(\w+)"', html)) - {'_csrf', 'action'}  # noqa: E731
    officer, editor, looker = (_main(_page(c, REPL)) for c in (kim, ed, lou))
    # the officer: every editor control, plus the board's amount and the entries for QuickBooks
    assert fields(officer) == {'budget_within_years', 'board_monthly', 'board_date', 'note', 'replace_year', 'replace_cost'}
    form = re.search(r'<form id="rule-board" method="post" action="/operations/plan/board"[^>]*hidden>(.*?)</form>', officer, re.S).group(1)
    assert 'name="board_monthly"' in form and 'value="$500"' in form and 'name="board_date" type="date"' in form and 'value="2026-11-05"' in form
    assert form.index('>Cancel</button>') < form.index('>Save</button>') and 'for="rule-board-monthly"' in form and 'for="rule-board-date"' in form
    assert officer.count('data-reveal="rule-board" aria-expanded="false" aria-controls="rule-board">Change<') == 1
    assert '<summary>Entries for QuickBooks</summary>' in officer and '<details class="small mt-2" id="entries">' in officer      # collapsed
    # the Operations editor: the plan's own controls, the board's amount as text
    assert fields(editor) == {'budget_within_years', 'note', 'replace_year', 'replace_cost'}
    assert 'rule-board' not in editor and 'Entries for QuickBooks' not in editor and 'Take off the plan' in editor
    assert 'The board set aside $500 a month, decided Nov 5, 2026, recorded by Kim Officer.' in _text(editor)
    # everyone else: plain text
    assert not re.search(r'<(form|input|button|select|textarea|details)\b', looker)
    assert 'The board set aside $500 a month' in _text(looker)
    state['board'] = None
    assert 'aria-controls="rule-board">Record it<' in _page(kim, REPL) and 'Record it' not in _page(ed, REPL)
    for c in (kim, ed, lou):
        html = _page(c, REPL)
        ids = re.findall(r'\sid="([^"]+)"', html)
        assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]


def test_the_entries_for_quickbooks_are_shown_not_made(plan):
    kim, ed, lou, state = plan
    y = state['y']
    box = lambda: _text(_main(_page(kim, REPL)).split('<details')[1].split('</details>')[0])  # noqa: E731
    text = box()
    assert 'The treasurer makes these two journal entries by hand. The app does not write them to QuickBooks.' in text
    assert "Twelve months of the board's amount: debit Retained Earnings, credit Equipment Replacement Reserve. The board has not set an amount yet." in text
    assert 'When an item that was saved for is bought. Take what was saved for it back out: debit Equipment Replacement Reserve, credit Retained Earnings' in text
    assert 'Blue tow truck' not in text      # bought before anything was saved for it: paid from the budget, no entry
    state['board'] = {'monthly': 500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}
    assert 'Twelve months at $500: debit Retained Earnings $6,000, credit Equipment Replacement Reserve $6,000.' in box()


# The Overview card and the report --------------------------------------------------------------------------------------

def _card(c):
    return _text(_main(_page(c, f'{O}/')).split('<div class="label">Replacements</div>')[1].split('</a>')[0])


def test_the_overview_card_says_what_is_true_now(plan):
    kim, ed, lou, state = plan
    m = f"${E.reserve_plan(state['items'])['monthly']:,.0f}"
    assert _card(lou) == f'{m} a month suggested by the plan; no board amount yet · $63,000 to buy soon · $0 in the reserve'
    state['board'] = {'monthly': 500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}
    state['sheet'] = _sheet(('Equipment Replacement Reserve', 12000.0))
    m = f"${E.reserve_plan(state['items'], balance=12000.0)['monthly']:,.0f}"
    for c in (kim, ed, lou):
        assert _card(c) == f'$500 a month set aside by the board; the plan suggests {m} · $63,000 to buy soon · $12,000 in the reserve'
    home = _page(lou, f'{O}/')
    assert 'from savings' not in home and 'big item' not in home and f'href="{REPL}"><div class="label">Replacements</div>' in home


def test_the_report_says_the_same(plan):
    import io

    import equip_pdf
    PdfReader = pytest.importorskip('pypdf').PdfReader      # only on the maintainer's computer; the server image has no PDF reader
    kim, ed, lou, state = plan

    def words(**kw):
        pdf = equip_pdf.build(state['items'], [], [], today=date(2026, 11, 4), **kw)
        return ' '.join(' '.join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf)).pages).split())
    text = words()
    assert 'The board has not set an amount.' in text and 'nothing booked yet' in text and 'suggested by the plan' in text
    assert 'from savings' not in text and 'big items' not in text and '*' not in text
    text = words(board={'monthly': 500.0, 'date': '2026-11-05', 'by': 'Kim Officer'},
                 reserve=E.reserve_balance(_sheet(('Equipment Replacement Reserve', 12000.0))))
    assert 'The board set aside $500 a month (Nov 5, 2026).' in text and 'In the reserve now: $12,000 (QuickBooks as of Nov 8, 2026).' in text
    assert 'At $500 a month the reserve is short by about' in text and 'set aside by the board' in text


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
        assert html.count('The plan now shows') == 1 and 'The plan now shows $3,000 for this year.' in text
        line = html.split('The plan now shows')[0].rsplit('<tr', 1)[1] + html.split('The plan now shows')[1].split('</tr>')[0]
        assert 'text-muted' in line and 'text-warning' not in line and 'text-danger' not in line
        for gone in ('add up to', 'edit the budget to match', 'turns amber', 'motion', 'stays in savings'):
            assert gone not in text, gone
        assert 'Changed since approved' not in text and 'Draft' in text               # the status is the plan's own
        assert 'Blue tow truck $45,000' in text and 'New radios $6,000' in text       # the equipment bought that year
    # a difference under a dollar says nothing
    state['plan']['OPS']['lines'][1]['net'] = -3000.4
    assert 'The plan now shows' not in _page(lou, state['url'])


def test_the_budget_page_shows_the_set_aside_under_the_total(budget):
    kim, ed, lou, state = budget
    rp = E.reserve_plan(state['items'])
    row = lambda c: _text(_main(_page(c, state['url'])).split('id="set-aside"')[1].split('</div>')[0].split('>', 1)[1])  # noqa: E731
    assert row(lou) == (f"Set aside for replacements: the plan suggests ${rp['monthly']:,.0f} a month (${rp['monthly'] * 12:,.0f} for the year). "
                        'The board has not set an amount. Not in the total.')
    state['board'] = {'monthly': 500.0, 'date': '2026-11-05', 'by': 'Kim Officer'}
    for c in (kim, ed, lou):
        assert row(c) == 'Set aside for replacements: $500 a month ($6,000 for the year), from the board on Nov 5, 2026. Not in the total.'
    html = _main(_page(lou, state['url']))
    assert html.index('class="total-row"') < html.index('id="set-aside"')
    total = _text(html.split('class="total-row"')[1].split('</tr>')[0])
    assert '-$53,736' in total and '6,000' not in total                              # the set-aside does not add to the total
    assert f'href="{REPL}">replacements</a>' in html


def test_the_reserve_counts_toward_what_is_due_first_and_the_suggestion_steps_down():
    from datetime import date
    items = [{'id': f'EQ-{n}', 'name': n, 'status': 'In use', 'cost': c, 'year': '', 'life_years': '', 'replace_year': y, 'replace_cost': c}
             for n, y, c in (('First', 2029, 12000.0), ('Second', 2030, 24000.0))]
    t = date(2027, 1, 15)
    rp = E.reserve_plan(items, t, {'budget_within_years': 1})
    assert [r['monthly'] for r in rp['later']] == [400.0, round(24000 / 42, 2)] and rp['saved'] == 0      # 30 and 42 months to July
    assert rp['steps'] == [(2029, round(400 + 24000 / 42, 2)), (2030, round(24000 / 42, 2))]
    rp = E.reserve_plan(items, t, {'budget_within_years': 1}, balance=15000.0)                       # pays First, and 3,000 of Second
    assert [r['saved'] for r in rp['later']] == [12000.0, 3000.0] and rp['monthly'] == 500.0 and rp['steps'] == [(2030, 500.0)]
    rp = E.reserve_plan(items, t, {'budget_within_years': 0})                                         # buy soon off: nothing is bought outright
    assert rp['near'] == [] and len(rp['later']) == 2


def test_buy_soon_can_be_switched_off(plan):
    kim, ed, lou, state = plan
    ed.post(f'{O}/plan/settings', data={'budget_within_years': '0'})
    assert state['cfg'] == {'budget_within_years': 0}
    text = _text(_main(_page(ed, REPL)))
    assert 'Buy soon is off: everything on the plan is saved for.' in text and 'Blue tow truck' in text
    assert 'Replacements' in _page(lou, f'{O}/')      # the Overview still renders
