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
    assert rp['later'][0]['monthly'] == round(40000 / 36, 2)              # Jan 2027 through Dec 2029
    assert rp['later'][1]['monthly'] == round(25000 / (18 * 12), 2)
    assert rp['monthly'] == round(rp['later'][0]['monthly'] + rp['later'][1]['monthly'], 2) and rp['near_total'] == 6000.0
    assert 'big_total' not in rp and not any('big' in r for r in rp['later'])       # no item is carved out as "big"


def test_replacement_year_on_the_item_wins():
    assert E.lifecycle(dict(item(), replace_year=2030.0), date(2026, 1, 1))['replace'] == 2030
    assert E.lifecycle(dict(item(cost=None, life=None, year=None), replace_year=2027.0))['replace'] == 2027


def test_capital_threshold():
    assert E.is_capital(item(cost=2500.0)) and not E.is_capital(item(cost=2499.0))


def test_next_due_rolls_the_year():
    assert E.next_due({'due': '2026-11-30', 'repeat_months': 12.0}) == '2027-11-30'      # a month end stays a month end
    assert E.next_due({'due': '2027-03-30', 'repeat_months': 24.0}) == '2029-03-30'
    assert E.next_due({'due': '2027-03-30', 'repeat_months': None}) == ''


def test_next_due_keeps_month_ends_and_steps_past_today():
    assert E.next_due({'due': '2027-01-31', 'repeat_months': 1}) == '2027-02-28'
    assert E.next_due({'due': '2027-02-28', 'repeat_months': 1}) == '2027-03-31'
    assert E.next_due({'due': '2028-01-30', 'repeat_months': 1}) == '2028-02-29'             # the 30th where the month has no 30th
    assert E.next_due({'due': '2027-01-15', 'repeat_months': 1}) == '2027-02-15'
    # marked Done long after it was due: the next date after today, not one step from the old date
    assert E.next_due({'due': '2024-03-10', 'repeat_months': 12}, after='2026-11-09') == '2027-03-10'
    assert E.next_due({'due': '2026-11-09', 'repeat_months': 1}, after='2026-11-09') == '2026-12-09'
    assert E.next_due({'due': '2026-12-01', 'repeat_months': 6}, after='2026-11-09') == '2027-06-01'   # done early: one step
    assert E.next_due({'due': 'sometime', 'repeat_months': 12}) == 'sometime'                # unreadable: left alone, no error


def test_dates_typed_into_the_sheet_by_hand_read_as_dates():
    assert E._date_text(46356) == '2026-11-30' and E._date_text('11/30/2026') == '2026-11-30'
    assert E._date_text('2026-11-30') == '2026-11-30' and E._date_text('') == '' and E._date_text('soon') == 'soon'


def test_fixed_cost_year():
    places = [{'cost_month': 100.0, 'cost_year': None}, {'cost_month': None, 'cost_year': 1200.0}]
    tasks = [{'cost': 3600.0, 'repeat_months': 12.0}, {'cost': 1000.0, 'repeat_months': 24.0}, {'cost': 50.0, 'repeat_months': None}]
    assert E.fixed_cost_year(places, tasks) == (2400.0, 4100.0)


def test_locations_nest_contents_under_trailers():
    item = lambda id_, name, place, cat='Other', nick='': dict(id=id_, name=name, place=place, category=cat, nickname=nick, status='In use')
    rows = [item('1', '22 race trailer', 'Thistle Hill', 'Trailer', 'Race Trailer'), item('2', 'Cones', 'Race Trailer'),
            item('3', 'Laptop', 'With Pat Example'), item('4', 'Banner', ''), item('5', 'Flags', 'Garage')]
    places = [{'id': 'PL-1', 'name': 'Thistle Hill Farm'}]
    where, people, other, unknown = E.locations(rows, places)
    (place, entries), = where
    assert place['name'] == 'Thistle Hill Farm' and entries[0]['it']['id'] == '1' and entries[0]['inside'][0]['it']['id'] == '2'
    assert people == [('Pat Example', [{'it': rows[2], 'inside': []}])]
    assert other[0][0] == 'Garage' and unknown[0]['it']['id'] == '4'


def test_documents_match_by_file_name():
    import equip_docs as D
    assert D.matches('Title', 'Title - front.jpg') and D.matches('Agreement', 'Lease 2026.pdf')
    assert D.matches('Insurance card', 'Insurance card 2026-27.pdf') and not D.matches('Registration', 'Title.jpg')


def test_budget_leaves_out_ended_and_not_yet_due_bills():
    def bill(**kw):
        return dict({'id': 'TK-1', 'task': 'x', 'about': '', 'owner': '', 'notes': '', 'category': 'Software and web', 'paid_how': '',
                     'account': 'Operations Professional Services', 'due': '', 'ends': ''}, **kw)
    tasks = [bill(task='Old host', repeat_months=1, cost=20, due='2026-11-27', ends='2026-12-31'),
             bill(task='Old domains', repeat_months=12, cost=368, due='2027-03-26', ends='2026-12-31'),
             bill(task='New plan', repeat_months=12, cost=336, due='2027-10-31'),
             bill(task='New domains', repeat_months=12, cost=50, due='2028-02-21'),
             bill(task='Mail', repeat_months=1, cost=8.4, due='2026-12-01'),
             bill(task='Winding down', repeat_months=1, cost=10, due='2027-01-05', ends='2027-03-31')]
    b = E.budget('2027', [], [], tasks)['Operations Professional Services']
    assert dict(b['items']) == {'New plan': 336, 'Mail': 100.8, 'Winding down': 30}
    assert b['amount'] == -466.8
    assert 'New domains' in dict(E.budget('2028', [], [], tasks)['Operations Professional Services']['items'])


def test_only_the_plan_year_is_bought_from_the_budget():
    rows = [dict(item(cost=23475.0, life=None, year=2012.0), replace_year=2029.0, replace_cost=40000.0),
            dict(item(cost=None, life=None, year=None, status='Planned'), replace_year=2028.0, replace_cost=15000.0),
            dict(item(cost=None, life=None, year=None, status='Planned'), replace_year=2027.0, replace_cost=6000.0)]
    rp = E.reserve_plan(rows, date(2026, 11, 15))                         # from October on the plan year is next year
    assert rp['year'] == 2027 and [r['year'] for r in rp['near']] == [2027] and [r['year'] for r in rp['later']] == [2028, 2029]
    assert rp['monthly'] == round(round(15000 / 24, 2) + round(40000 / 36, 2), 2) and 'cfg' not in rp and 'near_until' not in rp
    rp = E.reserve_plan(rows, date(2027, 5, 10))                          # mid-year: this year, saving from this month
    assert (rp['year'], rp['start']) == (2027, '2027-05') and [r['year'] for r in rp['near']] == [2027]
    assert rp['later'][0]['monthly'] == round(15000 / 20, 2)              # May 2027 through Dec 2028
    rp = E.reserve_plan(rows, date(2028, 2, 1))                           # a year on: the 2028 item is this year's purchase
    assert [r['year'] for r in rp['near']] == [2028] and [r['year'] for r in rp['later']] == [2029]


def test_months_are_counted_through_december_of_the_year_due():
    assert E._months_through_year('2027-01', 2027) == 12 and E._months_through_year('2027-01', 2029) == 36
    assert E._months_through_year('2027-05', 2028) == 20 and E._months_through_year('2027-12', 2027) == 1
    assert E._months_through_year('2027-05', 2026) == 1                   # never less than one
    assert not hasattr(E, '_months_to_july')


def test_the_budget_for_a_year_is_the_plan_items_due_that_year():
    rows = [dict(item(cost=None, life=None, year=None, status='Planned'), name=n, replace_year=y, replace_cost=c)
            for n, y, c in (('Radios', 2027.0, 6000.0), ('Tent', 2028.0, 15000.0), ('Truck', 2029.0, 40000.0))]
    day = date(2026, 11, 15)
    equipment = lambda y: E.budget(y, rows, [], [], day).get('Capital Equipment (no account set)', {}).get('items', [])  # noqa: E731
    assert equipment('2027') == [('Radios', 6000.0)] and equipment(2028) == [('Tent', 15000.0)] and equipment('2029') == [('Truck', 40000.0)]
    assert equipment('2030') == []
    rp = E.reserve_plan(rows, day)
    assert [r['it']['name'] for r in rp['near']] == ['Radios'] and [r['it']['name'] for r in rp['later']] == ['Tent', 'Truck']


def test_the_plan_has_no_settings():
    assert E.plan_settings() == E.PLAN_DEFAULTS == {}
    assert not hasattr(E, 'save_plan_settings') and not hasattr(E, '_plan_cfg')
