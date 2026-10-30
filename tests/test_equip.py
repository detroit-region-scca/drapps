from datetime import date

import equip_store as E


def item(cost=10000.0, life=20.0, year=2016.0, status='In use'):
    return {'cost': cost, 'life_years': life, 'year': year, 'status': status, 'name': 'Trailer'}


def test_lifecycle_straight_line():
    lc = E.lifecycle(item(), date(2026, 10, 29))
    assert lc == {'per_year': 500.0, 'left': 5000.0, 'replace': 2036}
    assert E.lifecycle(item(life=None)) is None


def test_lifecycle_never_below_zero():
    assert E.lifecycle(item(year=1990.0), date(2026, 1, 1))['left'] == 0.0


def test_reserve_plan_near_term_budgeted_later_saved_for():
    rows = [dict(item(cost=None, life=None, year=None, status='Planned'), replace_year=2027.0, replace_cost=6000.0),
            dict(item(cost=23475.0, life=None, year=2012.0), replace_year=2029.0, replace_cost=40000.0),
            dict(item(cost=13149.0, life=20.0, year=2024.0), replace_cost=25000.0),
            item(status='Gone', year=2010.0, life=20.0)]
    rp = E.reserve_plan(rows, date(2026, 11, 15))
    assert rp['start'] == '2027-01'
    assert [r['cost'] for r in rp['near']] == [6000.0]                   # 2027: buy it outright
    assert [r['year'] for r in rp['later']] == [2029, 2044]
    assert rp['later'][0]['monthly'] == round(40000 / 30, 2)              # Jan 2027 to Jul 2029
    assert rp['later'][1]['monthly'] == round(25000 / (17 * 12 + 6), 2)
    assert rp['big_total'] == 40000.0 and rp['monthly_without_big'] == rp['later'][1]['monthly']


def test_replacement_year_on_the_item_wins():
    assert E.lifecycle(dict(item(), replace_year=2030.0), date(2026, 1, 1))['replace'] == 2030
    assert E.lifecycle(dict(item(cost=None, life=None, year=None), replace_year=2027.0))['replace'] == 2027


def test_capital_threshold():
    assert E.is_capital(item(cost=2500.0)) and not E.is_capital(item(cost=2499.0))


def test_next_due_rolls_the_year():
    assert E.next_due({'due': '2026-11-30', 'repeat_months': 12.0}) == '2027-11-28'
    assert E.next_due({'due': '2027-03-30', 'repeat_months': 24.0}) == '2029-03-28'
    assert E.next_due({'due': '2027-03-30', 'repeat_months': None}) == ''


def test_fixed_cost_year():
    places = [{'cost_month': 100.0, 'cost_year': None}, {'cost_month': None, 'cost_year': 1200.0}]
    tasks = [{'cost': 3600.0, 'repeat_months': 12.0}, {'cost': 1000.0, 'repeat_months': 24.0}, {'cost': 50.0, 'repeat_months': None}]
    assert E.fixed_cost_year(places, tasks) == (2400.0, 4100.0)
