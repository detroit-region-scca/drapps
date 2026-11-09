"""Registering an event the program already has: the form asks "Is this the same event?" before a second #DR code."""
from tests.test_auth import _signed_in, app_  # noqa: F401  (fixture)


def test_maybe_same_window():
    from app import maybe_same
    ev = [{'id': 'a', 'program_code': 'RR', 'date': '2027-05-01', 'event_name': 'WHRRI Joint Regional', 'unique_code': '#DRAAA'},
          {'id': 'b', 'program_code': 'AX', 'date': '2027-05-29', 'event_name': 'Solo', 'unique_code': '#DRBBB'},
          {'id': 'c', 'program_code': 'RR', 'date': '2027-08-01', 'event_name': 'Later', 'unique_code': '#DRCCC'}]
    assert [m['code'] for m in maybe_same(ev, 'RR', '2027-05-29')] == ['#DRAAA']
    assert maybe_same(ev, 'RR', '2027-10-30') == []
    assert maybe_same(ev, 'RR', 'bad') == []


def test_form_asks_then_new_ok_creates(app_, monkeypatch):  # noqa: F811
    import app as A
    store = [{'id': 'a', 'program_code': 'RR', 'date': '2027-05-01', 'event_name': 'WHRRI Joint Regional',
              'unique_code': '#DRAAA', 'custom_tag': '', 'sequence_id': '2027-01-RR', 'creator_name': 'N'}]
    monkeypatch.setattr(A, 'load_events', lambda: [dict(e) for e in store])
    monkeypatch.setattr(A, 'save_events', lambda evs: store.__setitem__(slice(None), evs))
    monkeypatch.setattr(A, 'send_new_event_email', lambda e: None)
    monkeypatch.setattr(A, 'log_audit_event', lambda *a, **k: None)
    monkeypatch.setattr(A, '_link_plan_line', lambda *a, **k: None)
    monkeypatch.setattr(A, '_open_plan_lines', lambda evs: {})
    monkeypatch.setattr(A, '_budget_codes', lambda: set())
    c = _signed_in(app_, 'treasurer@example.org')
    form = {'program': 'RR', 'date': '2027-05-29', 'event_name': 'Jerry Shiloff', 'your_name': 'Jay'}
    r = c.post('/events/new', data=form)
    assert r.status_code == 302 and len(store) == 1
    page = c.get('/events/new').data.decode()
    assert 'Is this the same event?' in page and 'WHRRI Joint Regional' in page and 'name="new_ok"' in page
    r = c.post('/events/new', data=dict(form, new_ok='1'))
    assert r.status_code == 302 and len(store) == 2
