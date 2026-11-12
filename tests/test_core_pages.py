"""Error pages, static file caching, the Sheets-outage redirect, the treasurer report for limited people, audit lines."""
import pytest

OFFICIALS = [
    {'role': 'Treasurer', 'name': 'Pat Treasurer', 'email': 'treasurer@example.org'},
    {'role': 'Director', 'name': 'Rae Director', 'email': 'rx@example.org', 'program': 'RX'},
]


@pytest.fixture()
def app_(monkeypatch):
    import auth
    import officials
    monkeypatch.setattr(officials, 'all', lambda: [dict(o) for o in OFFICIALS])
    monkeypatch.setattr(auth, 'volunteers', lambda: {})
    from app import app
    app.config['TESTING'] = True
    return app


def _as(app_, email):
    c = app_.test_client()
    with c.session_transaction() as s:
        s['email'] = email
    return c


def test_not_found_page_has_a_way_back_signed_in_or_not(app_):
    for c in (app_.test_client(), _as(app_, 'rx@example.org')):
        r = c.get('/no/such/page')
        assert r.status_code == 404 and b'Page not found' in r.data and b'Back to the home page' in r.data


def test_forbidden_too_big_and_broken_pages(app_, monkeypatch):
    from flask import abort
    # let the 500 page show instead of the test's traceback
    monkeypatch.setitem(app_.config, 'TESTING', False)
    monkeypatch.setitem(app_.config, 'PROPAGATE_EXCEPTIONS', False)
    boom = {'403': lambda: abort(403), '413': lambda: abort(413), '500': lambda: 1 / 0}
    monkeypatch.setitem(app_.view_functions, 'privacy', lambda: boom[__import__('flask').request.args['e']]())
    c = app_.test_client()
    for code, words in ((403, b"You can&#39;t open that page"), (413, b'That upload is too big'), (500, b'Something went wrong')):
        r = c.get(f'/privacy?e={code}')
        assert r.status_code == code and words in r.data and b'Back to the home page' in r.data, code


def test_api_errors_stay_json(app_):
    r = app_.test_client().get('/api/v1/no-such-thing')
    assert r.status_code == 404 and r.is_json


def test_static_files_are_kept_a_year_and_their_links_change_with_the_file(app_):
    import os
    with app_.test_request_context('/'):
        from flask import url_for
        link = url_for('static', filename='drscca-logo.png')
    assert link == f"/static/drscca-logo.png?v={int(os.path.getmtime(os.path.join(app_.static_folder, 'drscca-logo.png')))}"
    r = app_.test_client().get(link)
    assert r.status_code == 200 and 'max-age=31536000' in r.headers['Cache-Control']


def test_a_sheets_outage_sends_people_back_where_they_were(app_, monkeypatch):
    import app as A
    from storage import StorageUnavailable

    def down(*a, **kw):
        raise StorageUnavailable('Google Sheets is unreachable, so events are read-only until it is back.')
    monkeypatch.setattr(A, 'load_events', lambda: [{'id': 'e1', 'date': '2027-05-01'}])
    monkeypatch.setattr(A, 'save_events', down)
    c = _as(app_, 'treasurer@example.org')
    r = c.post('/delete/e1', headers={'Referer': 'http://localhost/events/event/%23DRABC'})
    assert r.status_code == 302 and r.location == '/events/event/%23DRABC'
    r = c.post('/delete/e1', headers={'Referer': 'https://evil.example/x'})
    assert r.location == '/'


def test_event_deleted_is_not_shown_as_an_error(app_, monkeypatch):
    import app as A
    monkeypatch.setattr(A, 'load_events', lambda: [{'id': 'e1', 'date': '2027-05-01'}])
    monkeypatch.setattr(A, 'save_events', lambda events: None)
    monkeypatch.setattr(A, 'log_audit_event', lambda *a, **kw: None)
    c = _as(app_, 'treasurer@example.org')
    c.post('/delete/e1')
    with c.session_transaction() as s:
        assert s['_flashes'] == [('success', 'Event deleted.')]


def test_the_old_treasurer_report_goes_to_the_monthly_reports(app_):
    r = _as(app_, 'treasurer@example.org').get('/treasurer/report')
    assert r.status_code == 302 and r.headers['Location'].endswith('/treasurer/reports/')


# ---------- audit log lines ----------

LINES = [
    ('BALLOT_VOTE', {'ballot': 'BV-1', 'item': 'budget:2027:#DRABC', 'choice': 'yes', 'via': 'email link'}, 'voted Yes on the 2027 budget for #DRABC (by email link)', 'Board'),
    ('BALLOT_DECIDED', {'ballot': 'BV-1', 'item': 'motion:MO-2026-03', 'result': 'passed', 'count': '7-1'}, 'email vote on motion MO-2026-03 is decided: passed 7-1', 'Board'),
    ('BALLOT_REMINDED', {'ballot': 'BV-1', 'to': 1}, 'sent a reminder to 1 director who had not voted', 'Board'),
    ('MINUTES_UPLOADED', {'file': '2026-11 Board minutes DRAFT.pdf', 'approved': False}, 'uploaded minutes as a draft: 2026-11 Board minutes DRAFT.pdf', 'Board'),
    ('MINUTES_PUBLISHED', {'file': '2026-11 Board minutes.pdf'}, 'posted the approved minutes 2026-11 Board minutes.pdf', 'Board'),
    ('MINUTES_DRAFT_DELETED', {'file': 'x.pdf'}, 'deleted the draft minutes x.pdf', 'Board'),
    ('MEMBER_LIST_LOADED', {'file': 'list.csv', 'on_list': 412, 'members': 900}, 'loaded the SCCA member list list.csv: 412 current members on it', 'Members'),
    ('MEMBER_ADDED', {'member': '500100'}, 'added member 500100', 'Members'),
    ('MILESTONES_SAVED', {'month': '2026-11', 'names': 3}, 'saved the member milestones for 2026-11 (3 names)', 'Members'),
    ('POINTS_ENTERED', {'event': '#DRABC', 'rows': 12, 'points': 24}, 'entered 24 points for 12 members at #DRABC', 'Members'),
    ('POINTS_USED', {'member': '500100', 'points': -25, 'for': 'entry fee'}, 'used 25 points from member 500100 for entry fee', 'Members'),
    ('POINTS_FORFEITED', {'members': 2, 'points': -40}, 'deleted 40 points from 2 members who are not current', 'Members'),
    ('POINTS_BOOKS_EMAILED', {'entries': 9, 'journal_entries': 2}, 'emailed the treasurer the worker points to enter in QuickBooks (9 entries)', 'Members'),
    ('POINTS_RECONCILED', {'entries': 1, 'ref': 'JE 114'}, 'marked 1 points entry as entered in QuickBooks (JE 114)', 'Members'),
    ('AUTO_ADD_SAVED', {'people': 4}, 'saved the list of people who get points added every month (4 people)', 'Members'),
    ('RECORDS_CORRECTED', {'what': 'RX 2025 class SA', 'changes': 2}, 'corrected the records: RX 2025 class SA (2 changes)', 'Records'),
    ('RESULTS_ADDED', {'program': 'AX', 'event': 'Points 3', 'date': '2026-06-14', 'files': ['a.pdf', 'b.pdf']}, 'added results for Points 3 (AX 2026-06-14): 2 files', 'Records'),
    ('BUDGET_DRAFT', {'event_code': '#DRABC', 'was_approved': '2026-10-01'}, 'put the budget for #DRABC back in draft (it was approved on 2026-10-01)', 'Events'),
    ('EQUIP_DOCS_FOLDER_MADE', {'id': 'D-1', 'for': 'IT-1', 'name': 'Timing trailer'}, 'made a Drive folder for the documents of Timing trailer', 'Operations'),
    ('EQUIP_DOCS_UPLOADED', {'id': 'D-1', 'for': 'Timing trailer', 'doc': 'Registration', 'files': ['r.pdf']}, 'saved the document "Registration" for Timing trailer (1 file)', 'Operations'),
]


@pytest.mark.parametrize('action,details,want,app_name', LINES)
def test_audit_lines_read_as_sentences(app_, action, details, want, app_name):
    import audit_view
    with app_.test_request_context('/'):
        line = audit_view.plain({'action': action, 'user': 'Pat Treasurer', 'details': details})
    assert want in line and action.replace('_', ' ').lower() not in line
    assert audit_view.app_of(action)[0] == app_name


def test_describe_never_raises(app_):
    import audit_view
    odd = [{}, {'action': None, 'user': None, 'details': None}, {'action': 'PLAN_SAVED', 'details': {'net': 'lots'}},
           {'action': 'EVENT_EDITED', 'details': {'date': [None]}}, {'action': 'EVENT_EDITED', 'details': {'original': 'x', 'updated': 3}},
           {'action': 'BALLOT_VOTE', 'details': {'item': 7, 'choice': None}}, {'action': 'RESULTS_ADDED', 'details': {'files': 'one'}},
           {'action': 'FUNDS_PAID', 'details': ['not', 'a', 'dict']}, {'action': 12, 'user': ['x'], 'details': 'text'}]
    with app_.test_request_context('/'):
        for log in odd:
            assert str(audit_view.describe(log))
            assert isinstance(audit_view.app_of(log.get('action')), tuple)


def test_home_grays_out_tiles_outside_the_role(app_):
    html = _as(app_, 'rx@example.org').get('/').get_data(as_text=True)
    # a director has no Treasurer or Admin powers: the tiles show, grayed out and not links
    assert html.count('app-tile off') >= 2 and 'Not part of your role' in html
    assert 'href="/admin' not in html



def test_money_tabs_are_the_two_lists_and_the_year_sits_in_the_filter_row(app_):
    from flask import render_template
    base = dict(title='t', reqs=[], reps=[], counts={'Submitted': 0, 'Paid': 3}, year=2026, years=[2025, 2026], open_total=0, n_ready=0, open_cash=0)
    with app_.test_request_context('/funds/'):
        from flask import request
        request.url_rule = app_.url_map.bind('x').match('/funds/', return_rule=True)[0]
        html = render_template('fr.html', view='list', status='Submitted', **base)
    assert '>New request<' not in html and '>Report money received</a></li>' not in html
    assert html.count('id="year-pick"') == 1 and 'class="year-bar' not in html
    assert 'No open requests. Every request is paid.' in html
    with app_.test_request_context('/funds/in'):
        from flask import request
        request.url_rule = app_.url_map.bind('x').match('/funds/in', return_rule=True)[0]
        html = render_template('fr.html', view='in_list', status='Received', **base)
    assert 'Nothing waiting to be deposited.' in html and html.count('id="year-pick"') == 1
