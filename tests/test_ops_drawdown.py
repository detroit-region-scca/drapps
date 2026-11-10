"""A purchase due in the plan year is in that year's Operations budget at its full cost, but the reserve fund pays for it
first, up to what the fund holds; only the rest comes from the year's budget. What is left in the fund then counts
toward the later items. Four cases: nothing in the fund, less than this year's purchases, this year's purchases and part
of the later items, everything. Made-up names only."""
# ruff: noqa: F811  (the fixtures are imported, then named as arguments)
import io
import re
from datetime import date

import pytest

import equip_store as E
from test_ops_flow import O, _main, _page, _text, ops  # noqa: F401  (ops is a fixture)
from test_ops_reserve import PUR, RES, _card, _sheet, budget, plan  # noqa: F401  (plan and budget are fixtures)

T = date(2026, 11, 15)      # from October on the plan year is next year: 2027, saving from January
ROWS = [{'id': f'EQ-{n}', 'name': n, 'status': 'In use', 'cost': c, 'year': '', 'life_years': '', 'replace_year': y, 'replace_cost': c}
        for n, y, c in (('Truck', 2027, 45000.0), ('Radios', 2027, 6000.0), ('Trailer', 2028, 12000.0), ('Motorhome', 2030, 40000.0))]
CASES = (0.0, 20000.0, 70000.0, 200000.0)


def _check(rp, balance):
    for r in rp['near']:
        assert round(r['from_reserve'] + r['from_budget'], 2) == r['cost'] and r['from_reserve'] >= 0 and r['from_budget'] >= 0
    assert rp['near_from_reserve'] == round(sum(r['from_reserve'] for r in rp['near']), 2)
    assert rp['near_from_budget'] == round(sum(r['from_budget'] for r in rp['near']), 2)
    assert round(rp['near_from_reserve'] + rp['near_from_budget'], 2) == rp['near_total']
    later_cost = sum(r['cost'] for r in rp['later'])
    assert round(rp['near_from_reserve'] + rp['saved'], 2) == min(balance, rp['near_total'] + later_cost)


def test_the_reserve_fund_pays_for_this_years_purchases_first():
    plain = E.reserve_plan(ROWS, T)
    for balance in CASES:
        _check(E.reserve_plan(ROWS, T, balance), balance)
    # nothing in it: all from the budget, the later items as before
    assert [(r['from_reserve'], r['from_budget']) for r in plain['near']] == [(0.0, 45000.0), (0.0, 6000.0)]
    assert (plain['near_from_reserve'], plain['near_from_budget'], plain['saved']) == (0.0, 51000.0, 0.0)
    # less than this year's purchases: biggest first, none left for later, so the monthly amount is unchanged
    rp = E.reserve_plan(ROWS, T, 20000.0)
    assert [(r['from_reserve'], r['from_budget']) for r in rp['near']] == [(20000.0, 25000.0), (0.0, 6000.0)]
    assert (rp['near_from_reserve'], rp['near_from_budget'], rp['saved'], rp['monthly']) == (20000.0, 31000.0, 0.0, plain['monthly'])
    assert rp['steps'] == plain['steps'] and [s['year'] for s in rp['schedule']] == [s['year'] for s in plain['schedule']]
    # this year's purchases and part of the later ones: the $19,000 left goes to the item due first, then the next
    rp = E.reserve_plan(ROWS, T, 70000.0)
    assert (rp['near_from_reserve'], rp['near_from_budget']) == (51000.0, 0.0)
    assert [r['saved'] for r in rp['later']] == [12000.0, 7000.0] and rp['saved'] == 19000.0
    assert [r['monthly'] for r in rp['later']] == [0.0, round(33000 / 48, 2)] and rp['monthly'] == round(33000 / 48, 2)
    assert rp['steps'] == [(2030, round(33000 / 48, 2))] and E.paid_for_text(rp['schedule'][0]) == 'Trailer $12,000 (2028), Motorhome $40,000'
    assert rp == E.reserve_plan(ROWS, T, 70000.0) and rp['saved'] == E.reserve_plan(ROWS[2:], T, 19000.0)['saved']   # the same as $19,000 for the later items alone
    # everything
    rp = E.reserve_plan(ROWS, T, 200000.0)
    assert (rp['near_from_reserve'], rp['near_from_budget'], rp['saved'], rp['monthly'], rp['schedule']) == (51000.0, 0.0, 52000.0, 0.0, [])
    # every key the pages used before is still there
    assert {'start', 'start_label', 'year', 'near', 'later', 'monthly', 'steps', 'schedule', 'saved', 'near_total'} <= set(rp)


def test_cents_add_up():
    rows = [dict(ROWS[0], replace_cost=1000.10), dict(ROWS[1], replace_cost=333.33)]
    rp = E.reserve_plan(rows, T, 1000.15)
    assert [(r['from_reserve'], r['from_budget']) for r in rp['near']] == [(1000.10, 0.0), (0.05, 333.28)]
    _check(rp, 1000.15)
    assert E.reserve_plan(rows, T, -50.0)['near_from_reserve'] == 0.0           # a negative balance is no money


def test_the_budget_records_what_the_reserve_fund_pays_per_item():
    acc = 'Capital Equipment (no account set)'
    for balance, want in ((0.0, [0.0, 0.0]), (20000.0, [20000.0, 0.0]), (70000.0, [45000.0, 6000.0])):
        b = E.budget(2027, ROWS, [], [], T, balance=balance)[acc]
        assert b['amount'] == -51000.0 and b['items'] == [('Truck', 45000.0), ('Radios', 6000.0)]      # the full cost: the board approves all of it
        assert b['item_reserve'] == want and b['from_reserve'] == sum(want)
    assert E.budget(2027, ROWS, [], [], T)[acc]['from_reserve'] == 0.0                                   # no balance given: nothing from it
    later = E.budget(2028, ROWS, [], [], T, balance=200000.0)[acc]                                       # a later year: only this year draws on it
    assert later['items'] == [('Trailer', 12000.0)] and later['from_reserve'] == 0.0


# The pages, for each of the four cases --------------------------------------------------------------------------------

def _summary(html):
    return _text(html.split('id="summary">')[1].split('</div>\n    </div>')[0])


def _table(html, y):
    t = html.split(f'aria-label="Budgeted purchases for {y}"')[1].split('</table>')[0]
    return [_text(h) for h in re.findall(r'<th[^>]*>(.*?)</th>', t)], t


@pytest.mark.parametrize('balance', CASES)
def test_each_page_says_it_for_each_case(plan, balance):
    kim, ed, lou, state = plan
    y = state['y']
    state['sheet'] = _sheet(('Equipment Replacement Reserve', balance)) if balance else []
    rp = E.reserve_plan(state['items'], balance=balance)
    _check(rp, balance)
    want = {0.0: f'$51,000 planned for {y}. Paid from the {y} Operations budget.',
            20000.0: f'$51,000 planned for {y}: $20,000 from the reserve fund, $31,000 from the {y} Operations budget.',
            70000.0: f'$51,000 planned for {y}, all from the reserve fund.', 200000.0: f'$51,000 planned for {y}, all from the reserve fund.'}[balance]
    drawn = min(balance, 51000.0)
    for c in (kim, ed, lou):
        html = _main(_page(c, PUR))
        assert _summary(html) == f'{want} {y} Operations budget (officers edit it)'
        assert f'What we plan to buy in {y}. The board approves it with the {y} Operations budget.' in _text(html)
        assert 'not the reserve fund' not in html
        heads, table = _table(html, y)
        if not balance:
            assert 'From the reserve fund' not in heads and 'data-label="From the budget"' not in table
        else:
            assert heads[-3 if c is not lou else -2:][:2] == ['From the reserve fund', 'From the budget']
            assert table.count('data-label="From the reserve fund"') == 3 and table.count('data-label="From the budget"') == 3   # two rows and the total
            total = _text(table.split('>Total</td>')[1]).replace(' ', '')
            assert total == f'$51,000${drawn:,.0f}${51000 - drawn:,.0f}', total
        res = _text(_main(_page(c, RES)))
        line = f'${drawn:,.0f} of it pays for {y} purchases (see Budgeted purchases).'
        assert (line in res) == bool(balance) and ('of it pays for' in res) == bool(balance)
        if balance:
            assert res.index('In it now:') < res.index(line)
        assert ('The $19,000 left counts toward the items due first.' in res) == (balance == 70000.0)
        assert _card(c).startswith(f'$51,000 of purchases planned for {y}' + (f' (${drawn:,.0f} from the reserve fund)' if balance else '') + ' Reserve fund: ')
    if balance:
        assert f'href="{PUR}">Budgeted purchases</a>' in _main(_page(lou, RES))
    # the schedule's monthly amount uses only what is left after this year's purchases
    mo = rp['monthly']
    left = max(balance - 51000.0, 0.0)
    assert mo == E.reserve_plan(state['items'][2:], balance=left)['monthly']


def test_no_sideways_columns_without_a_reserve_draw_and_no_duplicate_ids(plan):
    kim, ed, lou, state = plan
    for balance in CASES:
        state['sheet'] = _sheet(('Equipment Replacement Reserve', balance)) if balance else []
        for c in (kim, ed, lou):
            for url in (PUR, RES, f'{O}/'):
                page = _page(c, url)
                ids = re.findall(r'\sid="([^"]+)"', page)
                assert len(ids) == len(set(ids)), (url, balance)


@pytest.mark.parametrize('balance', CASES)
def test_the_entries_for_quickbooks_list_what_to_take_out(plan, balance):
    kim, ed, lou, state = plan
    y = state['y']
    state['sheet'] = _sheet(('Equipment Replacement Reserve', balance)) if balance else []
    box = _text(_main(_page(kim, RES)).split('<details')[1].split('</details>')[0])
    if not balance:
        assert f'When a {y} purchase is bought. Nothing this year comes from the reserve fund.' in box
    else:
        assert (f'When a {y} purchase is bought. Take what the reserve fund pays for it back out: debit Equipment Replacement Reserve, '
                'credit Retained Earnings.') in box
        lines = re.findall(r'<li>(.*?)</li>', _main(_page(kim, RES)).split('id="entries-drawn"')[1].split('</ul>')[0])
        want = {20000.0: ['Blue tow truck: $20,000'], 70000.0: ['Blue tow truck: $45,000', 'New radios: $6,000'],
                200000.0: ['Blue tow truck: $45,000', 'New radios: $6,000']}[balance]
        assert [_text(x) for x in lines] == want
        assert ('The rest is paid from the budget and needs no entry.' in box) == (balance == 20000.0)
    for c in (ed, lou):
        assert 'Entries for QuickBooks' not in _page(c, RES)


@pytest.mark.parametrize('balance', CASES)
def test_the_operations_budget_says_what_the_reserve_fund_pays(budget, balance):
    kim, ed, lou, state = budget
    state['sheet'] = _sheet(('Equipment Replacement Reserve', balance)) if balance else []
    for c in (kim, ed, lou):
        html = _main(_page(c, state['url']))
        row = html.split('>Capital Equipment:Race Equipment</td>')[1]
        assert '-$51,000' in _text(row.split('</tr>')[0])                     # the account stays the full cost
        items = _text(row.split('</tr>')[1])
        want = {0.0: 'Blue tow truck $45,000 · New radios $6,000',
                20000.0: 'Blue tow truck $45,000 (from the reserve fund: $20,000) · New radios $6,000 $20,000 of this comes from the reserve fund.',
                70000.0: 'Blue tow truck $45,000 (from the reserve fund: $45,000) · New radios $6,000 (from the reserve fund: $6,000) $51,000 of this comes from the reserve fund.',
                200000.0: 'Blue tow truck $45,000 (from the reserve fund: $45,000) · New radios $6,000 (from the reserve fund: $6,000) $51,000 of this comes from the reserve fund.'}[balance]
        assert items == want, items
        assert 'reserve fund' not in _text(html.split('>Operations Storage</td>')[1].split('</tr>')[1])      # bills: nothing about it
        total = _text(html.split('class="total-row"')[1].split('</tr>')[0])
        assert '-$53,736' in total


def test_the_report_says_the_same(plan):
    import equip_pdf
    PdfReader = pytest.importorskip('pypdf').PdfReader      # only on the maintainer's computer; the server image has no PDF reader
    kim, ed, lou, state = plan
    y = state['y']

    def words(balance):
        pdf = equip_pdf.build(state['items'], [], [], reserve=E.reserve_balance(_sheet(('Equipment Replacement Reserve', balance))))
        return ' '.join(' '.join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf)).pages).split())
    assert f'$51,000 planned for {y}. Paid from the {y} Operations budget.' in words(0.0)
    text = words(20000.0)
    assert f'$51,000 planned for {y}: $20,000 from the reserve fund, $31,000 from the {y} Operations budget.' in text
    assert 'From the reserve fund' in text and f'$20,000 of it pays for {y} purchases.' in text
    assert f'of purchases planned for {y} ($20,000 from the reserve fund)' in text
    text = words(70000.0)
    assert f'$51,000 planned for {y}, all from the reserve fund.' in text and 'The $19,000 left counts toward the items due first.' in text
