"""Same event last year: only an earlier event of the same program can be chosen, and the history follows only
what was chosen (stops at a gap or a loop)."""
import bud
import bud_store as B


def test_history_follows_the_chosen_links(monkeypatch):
    buds = {'#C': {'last_year_event': '#B'}, '#B': {'last_year_event': '#A'}, '#A': {'last_year_event': ''}}
    monkeypatch.setattr(B, 'budgets', lambda: buds)
    assert [h['code'] for h in B.history('#C')] == ['#C', '#B', '#A']
    buds['#A']['last_year_event'] = '#C'          # a loop typed by hand in the sheet
    assert [h['code'] for h in B.history('#C')] == ['#C', '#B', '#A']
    assert [h['code'] for h in B.history('#X')] == ['#X']   # no budget: just itself


def test_choices_are_last_years_events_of_the_same_program(monkeypatch):
    evs = [{'unique_code': '#A', 'program_code': 'RY', 'date': '2025-02-01', 'event_name': 'Sno 2025'},
           {'unique_code': '#B', 'program_code': 'RY', 'date': '2026-02-01', 'event_name': 'Sno 2026'},
           {'unique_code': '#C', 'program_code': 'RY', 'date': '2027-02-01', 'event_name': 'Sno 2027'},
           {'unique_code': '#D', 'program_code': 'AX', 'date': '2026-05-01', 'event_name': 'Solo'}]
    monkeypatch.setattr(bud, 'load_events', lambda: evs)
    monkeypatch.setattr(B, 'budgets', lambda: {'#B': {}})
    got = bud._last_year_events('#C', evs[2])
    assert [x['code'] for x in got] == ['#B'] and got[0]['has_budget']
    assert [x['code'] for x in bud._last_year_events('#C', evs[2], keep='#A')] == ['#A', '#B']   # an older link stays listed


def test_link_only_change_keeps_the_budget_unchanged():
    old = {'chair': 'Pat', 'location': 'Here', 'notes': '', 'event_name': 'Sno', 'program': 'RY', 'date': '2027-01-09'}
    lines = [{'section': 'Income', 'description': 'Entries', 'last_year': 900.0, 'budget': 1000.0, 'actual': None, 'sort': 0}]
    new_lines = [{'section': 'Income', 'description': 'Entries', 'last_year': 900.0, 'budget': 1000.0, 'actual': None}]
    assert B._same_budget(old, dict(old, last_year_event='#A'), lines, new_lines)
    assert not B._same_budget(old, dict(old, chair='Sam'), lines, new_lines)
    assert not B._same_budget(old, old, lines, [dict(new_lines[0], budget=1100.0)])
