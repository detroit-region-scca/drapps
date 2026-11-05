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


def test_actuals_page_saves_actuals_only(monkeypatch):
    import auth
    from app import app
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Pat', 'level': 'official', 'roles': ['Director'],
                                                   'perms': {'events'}} if e else None)
    monkeypatch.setattr(bud, '_guard', lambda: None)
    monkeypatch.setattr(bud, '_event', lambda code: {'event_name': 'Sno', 'date': '2027-01-09', 'program_code': 'RY'})
    budget = {'event_code': '#C', 'event_name': 'Sno', 'program': 'RY', 'date': '2027-01-09', 'chair': 'Pat', 'location': '',
              'notes': '', 'last_year_event': '#B', 'updated_by': 'Pat',
              'lines': [{'section': 'Income', 'description': 'Entries', 'last_year': None, 'budget': 1000.0, 'actual': None, 'sort': 0},
                        {'section': 'Expense', 'description': 'Site', 'last_year': None, 'budget': 400.0, 'actual': None, 'sort': 1}]}
    monkeypatch.setattr(B, 'get', lambda code: budget)
    saved = []
    monkeypatch.setattr(B, 'save', lambda header, lines, files=(), actuals_only=False: saved.append((header, lines, actuals_only)))
    monkeypatch.setattr(bud, 'log_audit_event', lambda *a, **k: None)
    app.config['TESTING'] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='pat@example.org', who='Pat')
    from werkzeug.datastructures import MultiDict
    r = c.post('/events/event/%23C/actuals', data=MultiDict([('act', '950'), ('act', '410.50'), ('new_exp_desc', 'Extra cones'), ('new_exp_act', '35')]))
    assert r.status_code == 302 and saved
    header, lines, only = saved[0]
    assert only and header['last_year_event'] == '#B'
    assert [(ln['description'], ln['budget'], ln['actual']) for ln in lines] == [('Entries', 1000.0, 950.0), ('Site', 400.0, 410.5), ('Extra cones', None, 35.0)]


def test_entering_actuals_never_marks_the_budget_changed():
    old = {'chair': 'Pat', 'location': 'Here', 'notes': '', 'event_name': 'Sno', 'program': 'RY', 'date': '2027-01-09'}
    lines = [{'section': 'Income', 'description': 'Entries', 'last_year': 900.0, 'budget': 1000.0, 'actual': None, 'sort': 0}]
    changed = [{'section': 'Income', 'description': 'Entries', 'last_year': 900.0, 'budget': 1000.0, 'actual': 950.0}]
    assert not B._same_budget(old, old, lines, changed)          # through the budget form it would count as a change...
    import inspect
    assert 'actuals_only' in inspect.signature(B.save).parameters  # ...so actuals save with actuals_only=True


def test_event_warnings(monkeypatch):
    from datetime import date as D
    monkeypatch.setattr(B, 'enabled', lambda: True)
    today = D(2026, 11, 5)
    ev = lambda d: {'date': d, 'event_name': 'X'}  # noqa: E731
    lines = [{'section': 'Income', 'actual': None}]
    assert bud._warnings(ev('2027-01-09'), None, today) == [('No budget yet', 'budget')]
    assert bud._warnings(ev('2026-05-01'), None, today) == [('No budget', 'budget')]
    assert bud._warnings(ev('2026-05-01'), {'lines': lines}, today)[0][1] == 'results'
    assert bud._warnings(ev('2026-05-01'), {'lines': [{'actual': 950.0}]}, today) == []
    assert bud._warnings(ev('2027-01-09'), {'lines': lines}, today) == []
    assert bud._warnings(dict(ev('2026-05-01'), program_budget=True), None, today) == []


def test_heading_widens_a_narrow_tab(monkeypatch):
    class WS:
        col_count, added, written = 12, [], []
        def add_cols(self, n): self.added.append(n); WS.col_count += n
        def row_values(self, i): return B.BUD_COLS[:12]
        def update(self, values, rng, **kw): self.written.append((values, rng))
    monkeypatch.setattr(B, '_cache', {})
    ws = WS()
    B._heading(ws)
    assert ws.added == [len(B.BUD_COLS) - 12] and ws.written == [([B.BUD_COLS[12:]], 'M1')]
