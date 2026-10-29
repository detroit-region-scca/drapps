import plan_store as P


def ev(code, name, d, prog='RX'):
    return {'unique_code': code, 'custom_tag': '', 'event_name': name, 'date': d, 'program_code': prog}


def line(item, month, sort, code='', prog='RX', year='2026'):
    return {'year': year, 'program': prog, 'item': item, 'month': month, 'net': 100.0, 'event_code': code, 'sort': sort}


def test_score_words_month_and_code():
    e = ev('#DRAAA', 'Longest Day RallyCross', '2026-06-27')
    assert P.match_score('The Longest Day (RX#4)', 6, e) == 5
    assert P.match_score('Sample Event 5', 6, e) == 2
    assert P.match_score('Budget for #DRAAA', None, e) == 100


def test_suggest_needs_a_clear_winner():
    e = ev('#DRAAA', 'Summer Heat', '2026-07-11')
    assert P.suggest(e, [line('Summer Heat', 7, 0), line('Fall Classic', 9, 1)])['sort'] == 0
    assert P.suggest(e, [line('Event 5', 7, 0), line('Event 6', 7, 1)]) is None


def test_plan_matches_links_clear_and_asks_unclear():
    events = [ev('#DRAAA', 'Summer Heat', '2026-07-11'), ev('#DRBBB', 'Twilight Test and Tune', '2026-08-12'),
              ev('#DRCCC', 'Fall Classic', '2026-09-05'), ev('#DRDDD', 'Brand New Thing', '2026-10-03')]
    lines = [line('Summer Heat', 7, 0), line('June School and Twilight', 6, 1), line('Fall Classic', 9, 2, code='#DRCCC')]
    link, ask = P.plan_matches(events, lines)
    assert [(e['unique_code'], ln['sort']) for e, ln in link] == [('#DRAAA', 0)]
    assert [(e['unique_code'], [x['sort'] for x in near]) for e, near in ask] == [('#DRBBB', [1])]


def test_plan_matches_skips_other_programs_and_years():
    events = [ev('#DRAAA', 'Summer Heat', '2026-07-11', prog='AX'), ev('#DRBBB', 'Summer Heat', '2027-07-10')]
    link, ask = P.plan_matches(events, [line('Summer Heat', 7, 0)])
    assert not link and not ask
