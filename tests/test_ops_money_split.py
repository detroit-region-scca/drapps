"""Money in three parts: Bills, the plan year's purchases and the Reserve fund, the last two a page each with a heading
and a one-line summary that tells them apart. Old addresses land on the purchases; an item whose year moves it from one
page to the other says where it went. Made-up names only."""
# ruff: noqa: F811  (the fixtures are imported, then named as arguments)
import re

import equip_store as E
from test_ops_flow import O, _flashes, _main, _page, _text, ops  # noqa: F401  (ops is a fixture)
from test_ops_reserve import PUR, RES, _sheet, plan  # noqa: F401  (plan is a fixture)


def _tabs(html):
    bar = html.split('role="group" aria-label="Money">')[1].split('</div>')[0]
    return re.findall(r'<a class="btn btn-sm btn-outline-light( active)?"(?: aria-current="true")? href="([^"]+)">([^<]+)</a>', bar)


def test_money_has_three_sub_tabs(plan):
    kim, ed, lou, state = plan
    y = state['y']
    for url, on in ((f'{O}/money', f'{O}/money'), (PUR, PUR), (RES, RES)):
        for c in (kim, ed, lou):
            tabs = _tabs(_main(_page(c, url)))
            assert [(h, t) for _, h, t in tabs] == [(f'{O}/money', 'Bills'), (PUR, 'Budgeted purchases'), (RES, 'Reserve fund')], url
            assert [h for a, h, _ in tabs if a] == [on], url
            assert re.search(r'nav-link active" href="[^"]*">Money<', _page(c, url)), url


def test_each_page_has_its_own_heading_title_and_summary(plan):
    kim, ed, lou, state = plan
    y = state['y']
    state['note'] = 'Radios first.'
    for c in (kim, ed, lou):
        page = _page(c, PUR)
        html = _main(page)
        text = _text(html)
        assert '<title>Budgeted purchases' in page and f'<h1 class="page-title m-0">Budgeted purchases for {y}</h1>' in html
        summary = html.split('id="summary"')[1].split('</div>\n    </div>')[0]
        assert 'class="card plan-sum p-3 mb-3"' in html and 'plan-sum reserve' not in html
        assert _text(summary.split('>', 1)[1]) == (f'$51,000 planned for {y}. Paid from the {y} Operations budget, not the reserve fund. '
                                                   f'{y} Operations budget (officers edit it)')
        # the intro and the note come right under the heading, before the summary
        assert text.index(f'What we plan to buy in {y}. The board approves it with the {y} Operations budget.') < text.index('Note from the committee: Radios first.') \
            < text.index(f'$51,000 planned for {y}')

        page = _page(c, RES)
        html = _main(page)
        text = _text(html)
        assert '<title>Reserve fund' in page and '<h1 class="page-title m-0">Reserve fund</h1>' in html
        assert 'class="card plan-sum reserve p-3 mb-3"' in html
        mo = f"${E.reserve_plan(state['items'])['schedule'][0]['monthly']:,.0f}"
        assert (f'In it now: $0, nothing booked yet Put in {mo} a month in {y} '
                f'For everything due after {y}, saved for month by month through the year each item is due.') in text
        assert text.index('What we expect to replace in later years, and what to save each month for it. The board approves the monthly amount') < text.index('Note from the committee') < text.index('In it now:') \
            < text.index('What to put in each month') < text.index('Total') and f'What to put in each month, from {E.reserve_plan([])["start_label"]}' in text
    state['sheet'] = _sheet(('Equipment Replacement Reserve', 12000.0))
    text = _text(_main(_page(lou, RES)))
    assert 'In it now: $12,000 (QuickBooks account Equipment Replacement Reserve, as of Nov 8, 2026)' in text


def test_the_old_addresses_land_on_the_purchases(plan):
    kim, ed, lou, state = plan
    for c in (kim, ed, lou):
        for old, new in ((f'{O}/money/replacement', PUR), (f'{O}/plan', PUR), (f'{O}/plan?year=2027', f'{PUR}?year=2027')):
            r = c.get(old)
            assert r.status_code == 302 and r.headers['Location'] == new, old
    r = kim.post(f'{O}/plan/settings', data={})
    assert r.headers['Location'] == PUR


def test_add_a_planned_purchase_starts_in_the_plan_year_on_the_purchases_page(plan):
    kim, ed, lou, state = plan
    y = state['y']
    for c in (kim, ed):
        assert f'href="{O}/items/new?status=Planned&amp;replace_year={y}">Add a planned purchase</a>' in _page(c, PUR)
        assert f'href="{O}/items/new?status=Planned">Add a planned purchase</a>' in _page(c, RES)
    form = _page(ed, f'{O}/items/new?status=Planned&replace_year={y}')
    assert f'<input name="replace_year" class="form-control" inputmode="numeric" value="{y}"' in form and '<option selected>Planned</option>' in form
    assert 'name="replace_year" class="form-control" inputmode="numeric" value=""' in _page(ed, f'{O}/items/new?status=Planned')
    assert 'value=""' in _page(ed, f'{O}/items/new?replace_year=soon').split('name="replace_year"')[1].split('>')[0]
    assert 'Add a planned purchase' not in _page(lou, PUR) + _page(lou, RES)
    state['items'] = state['items'][2:]                                                  # nothing due in the plan year: the button is still there
    assert 'Add a planned purchase' in _page(ed, PUR) and f'No planned purchases for {y}.' in _text(_main(_page(ed, PUR)))


def test_the_rows_post_the_same_fields_back_to_their_own_page(plan):
    kim, ed, lou, state = plan
    for url in (PUR, RES):
        html = _main(_page(ed, url))
        row = html.split('<tr id="EQ-001">' if url == PUR else '<tr id="EQ-004">')[1].split('</tr>')[0]
        iid = 'EQ-001' if url == PUR else 'EQ-004'
        assert f'<form id="rp-{iid}" method="post" action="{O}/items/{iid}"' in row and f'form="rp-{iid}" name="replace_year"' in row
        assert f'name="back" value="{url}#{iid}"' in row and f'name="action" value="off_plan"><input type="hidden" name="back" value="{url}">' in row


def test_a_new_year_that_moves_an_item_says_where_it_went(plan):
    kim, ed, lou, state = plan
    y = state['y']
    # a plan-year purchase put off to a later year: off to the reserve fund, back on the purchases page
    r = ed.post(f'{O}/items/EQ-001', data={'action': 'replacement', 'replace_year': str(y + 2), 'replace_cost': '$45,000', 'back': f'{PUR}#EQ-001'})
    assert r.headers['Location'] == PUR and state['saves'][-1][1]['replace_year'] == str(y + 2)
    assert _flashes(ed) == f'Blue tow truck: replacement updated. Moved to <a href="{RES}#EQ-001">the reserve fund</a>.'
    # a reserve fund item brought forward to the plan year: off to the purchases, back on the reserve page
    r = ed.post(f'{O}/items/EQ-003', data={'action': 'replacement', 'replace_year': str(y), 'replace_cost': '12000', 'back': f'{RES}#EQ-003'})
    assert r.headers['Location'] == RES
    shown = _main(_page(ed, RES))
    assert f'Timing trailer: replacement updated. Moved to <a href="{PUR}#EQ-003">budgeted purchases</a>.' in shown        # a link, not escaped
    # a change that stays on the same page says what it always did and keeps its place on the page
    r = ed.post(f'{O}/items/EQ-004', data={'action': 'replacement', 'replace_year': str(y + 5), 'replace_cost': '40000', 'back': f'{RES}#EQ-004'})
    assert r.headers['Location'] == f'{RES}#EQ-004' and _flashes(ed) == 'Long trailer: replacement updated.'
    r = ed.post(f'{O}/items/EQ-002', data={'action': 'replacement', 'replace_year': str(y), 'replace_cost': '6500', 'back': f'{PUR}#EQ-002'})
    assert r.headers['Location'] == f'{PUR}#EQ-002' and _flashes(ed) == 'New radios: replacement updated.'
    # from the item's own page it goes back there
    r = ed.post(f'{O}/items/EQ-005', data={'action': 'replacement', 'replace_year': str(y), 'replace_cost': '40000', 'back': f'{O}/items/EQ-005#replacement'})
    assert r.headers['Location'] == f'{O}/items/EQ-005' and f'Moved to <a href="{PUR}#EQ-005">budgeted purchases</a>.' in _flashes(ed)
    # taking it off the plan goes back to the page it came from, as before
    r = ed.post(f'{O}/items/EQ-006', data={'action': 'off_plan', 'back': RES})
    assert r.headers['Location'] == RES and 'Pop-up tent is off the replacement plan. It is still in Gear.' in _flashes(ed)


def test_the_overview_card_links_each_line_to_its_page(plan):
    kim, ed, lou, state = plan
    y = state['y']
    for c in (kim, ed, lou):
        home = _main(_page(c, f'{O}/'))
        card = home[home.rindex('<div class="col-6', 0, home.index('<div class="label">Replacements</div>')):].split('</a></div></div></div>')[0] + '</a>'
        links = [(h, _text(t)) for h, t in re.findall(r'<a [^>]*href="([^"]+)">(.*?)</a>', card, re.S)]
        mo = f"${E.reserve_plan(state['items'])['monthly']:,.0f}"
        assert links == [(PUR, f'Replacements $51,000 of purchases planned for {y}'), (RES, f'Reserve fund: {mo} a month, $0 in it now')]
