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


def test_replacement_plan_and_reserve():
    rows = [item(cost=20000.0, life=10.0, year=2020.0), item(cost=10000.0, life=5.0, year=2024.0), item(status='Gone', year=2010.0, life=20.0)]
    plan, reserve = E.replacement_plan(rows, date(2026, 6, 1))
    assert list(plan) == [2029, 2030]
    assert reserve == round(30000 / 5, 2)


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
