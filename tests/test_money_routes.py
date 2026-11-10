"""The money pages' checks: a request is paid or rejected once and for a sane amount, limited roles see only their own,
the audit log names the signed-in person, and forms come back with what was typed. Made-up names only."""
import io

import pytest
from werkzeug.datastructures import MultiDict

import bud
import bud_store as B
import fr
import fr_logic as FL
import plan


def _client(monkeypatch, perms, name='Pat Sample'):
    import auth
    from app import app
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': name, 'level': 'official', 'roles': ['Director'],
                                                   'perms': set(perms)} if e else None)
    app.config['TESTING'] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='pat@example.org', who=name)
    return c


def _flashes(c):
    with c.session_transaction() as s:
        return ' '.join(m for _, m in s.pop('_flashes', []))


REQ = {'id': 'FR-2026-001', 'status': 'Submitted', 'submitted': '2026-11-01', 'net': 300.0, 'total': 300.0, 'approvals': '',
       'payee_name': 'Sam Sample', 'submitter_name': 'Sam Sample', 'submitter_email': 'sam@example.org', 'entered_by': 'Sam Sample',
       'event_code': '#DRAAA', 'program': 'AX', 'lines': []}


@pytest.fixture
def pay(monkeypatch):
    """The treasurer on a funds request page, with the store replaced by a dict."""
    state = {'r': dict(REQ), 'closed': [], 'log': []}
    monkeypatch.setattr(fr, '_guard', lambda: None)
    monkeypatch.setattr(fr.F, 'refresh', lambda: None)
    monkeypatch.setattr(fr.F, 'get', lambda rid: dict(state['r']))

    def close(rid, status, expect=None, **kw):
        if expect and state['r']['status'] != expect:
            return None
        state['r'].update(status=status, **kw)
        state['closed'].append(status)
        return state['r']
    monkeypatch.setattr(fr.F, 'close', close)
    monkeypatch.setattr(fr, 'Approvals', lambda: type('A', (), {'items': lambda self, r: []})())
    monkeypatch.setattr(fr, 'log_audit_event', lambda action, who, d: state['log'].append(action))
    monkeypatch.setattr(fr, 'tell_submitter', lambda rid: state['log'].append('told'))
    state['c'] = _client(monkeypatch, {'funds', 'funds_all', 'pay'})
    return state


def _close(pay, **form):
    return pay['c'].post('/funds/FR-2026-001/close', data=form)


def test_mark_paid_twice_pays_logs_and_emails_once(pay):
    _close(pay, action='paid', paid_ref='Check 6080')
    _close(pay, action='paid', paid_ref='Check 6080')
    assert pay['closed'] == ['Paid'] and pay['log'] == ['FUNDS_PAID', 'told']
    assert 'is paid now, so nothing was changed' in _flashes(pay['c'])


def test_a_rejected_request_cannot_be_paid_and_an_open_one_cannot_be_reopened(pay):
    _close(pay, action='reopen')
    assert pay['closed'] == []
    _close(pay, action='reject', note='not ours')
    _close(pay, action='paid', paid_ref='Check 6080')
    assert pay['closed'] == ['Rejected'] and pay['r']['status'] == 'Rejected'
    _close(pay, action='reopen')
    assert pay['closed'] == ['Rejected', 'Submitted']


def test_someone_else_closing_it_first_is_caught_under_the_lock(pay, monkeypatch):
    monkeypatch.setattr(fr.F, 'close', lambda rid, status, expect=None, **kw: None)     # the other worker got there first
    _close(pay, action='paid', paid_ref='Check 6080')
    assert pay['log'] == [] and 'was just changed by someone else' in _flashes(pay['c'])


@pytest.mark.parametrize('amount, said', [('0', 'above zero'), ('-20', 'above zero'), ('2600', 'second signer'), ('abc', 'the amount')])
def test_paid_amount_is_checked(pay, amount, said):
    _close(pay, action='paid', paid_ref='Check 6080', paid_amount=amount)
    assert pay['closed'] == [] and said in _flashes(pay['c'])


def test_a_large_payment_goes_through_with_the_second_signer(pay):
    pay['r'].update(net=2600.0, approvals=FL.add('', 'second', 'Robin Exec', '2026-11-08'))
    _close(pay, action='paid', paid_ref='Check 6080')
    assert pay['closed'] == ['Paid'] and pay['r']['paid_amount'] == 2600.0


def _new_form(**kw):
    return MultiDict(dict({'method': 'check', 'program': 'AX', 'submitter_name': 'Sam Sample', 'submitter_email': 'sam@example.org',
                           'payee_name': 'Example Rentals', 'payee_address': '1 Main St', 'line_date': '2026-11-01', 'line_desc': 'Cones',
                           'line_cat': 'Supplies', 'line_amount': '80', 'advances': '0', 'token': 'page-1'}, **kw))


@pytest.fixture
def filing(monkeypatch):
    state = {'made': [], 'told': []}
    monkeypatch.setattr(fr, '_guard', lambda: None)
    monkeypatch.setattr(fr, 'load_events', lambda: [])
    monkeypatch.setattr(fr, 'load_programs', lambda: {'AX': 'Autocross'})
    monkeypatch.setattr(fr, 'directors', lambda: {})
    monkeypatch.setattr(fr.O, 'signers', lambda: [])
    monkeypatch.setattr(fr.O, 'director_email', lambda p: '')

    def create_once(req, lines, receipts, token=''):
        state['made'].append((req, token))
        return 'FR-2026-007', len(state['made']) > 1
    monkeypatch.setattr(fr.F, 'create_once', create_once)
    monkeypatch.setattr(fr, 'notify', lambda *a: state['told'].append(a[0]))
    monkeypatch.setattr(fr, 'mark_ready', lambda rid: False)
    monkeypatch.setattr(fr, 'log_audit_event', lambda *a, **k: None)
    state['c'] = _client(monkeypatch, {'funds'})
    return state


def test_advances_over_the_total_are_refused_and_dropped_files_are_mentioned(filing):
    data = _new_form(advances='100')
    data.add('receipts', (io.BytesIO(b'x'), 'receipt.pdf'))
    r = filing['c'].post('/funds/new', data=data, content_type='multipart/form-data')
    html = r.get_data(as_text=True)
    assert r.status_code == 400 and not filing['made']
    assert 'Advances cannot be more than the total' in html and 'Attach them again' in html and 'value="Cones"' in html


def test_the_same_page_submitted_twice_tells_the_treasurer_once(filing):
    a = filing['c'].post('/funds/new', data=_new_form())
    b = filing['c'].post('/funds/new', data=_new_form())
    assert a.status_code == b.status_code == 302 and a.location == b.location
    assert [t for _, t in filing['made']] == ['page-1', 'page-1'] and filing['told'] == ['FR-2026-007']
    assert 'no second request was made' in _flashes(filing['c'])


def _funds_list(monkeypatch, perms, name):
    import auth
    from app import app
    reqs = [dict(REQ), dict(REQ, id='FR-2026-002', submitter_email='lee@example.org', entered_by='Lee Other', payee_name='Lee Other')]
    monkeypatch.setattr(bud.F, 'enabled', lambda: True)
    monkeypatch.setattr(bud.F, 'requests', lambda: reqs)
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': name, 'level': 'official', 'roles': [], 'perms': set(perms)})
    with app.test_request_context('/'):
        from flask import session
        session.update(email='sam@example.org', who=name)
        return [r['id'] for r in bud._funds('#DRAAA')]


def test_an_event_page_shows_a_limited_role_only_its_own_requests(monkeypatch):
    monkeypatch.setattr(bud, 'load_programs', lambda: {'AX': 'Autocross'})
    assert _funds_list(monkeypatch, {'funds'}, 'Sam Sample') == ['FR-2026-001']
    assert _funds_list(monkeypatch, {'funds', 'funds_all'}, 'Sam Sample') == ['FR-2026-001', 'FR-2026-002']


def _budget(**kw):
    return dict({'event_code': '#C', 'event_name': 'Test Solo', 'program': 'AX', 'date': '2027-05-01', 'chair': '', 'location': '',
                 'notes': '', 'last_year_event': '', 'updated_by': 'Pat Sample', 'approved': '', 'draft': '2026-11-01 by Pat Sample',
                 'lines': [{'section': 'Income', 'description': 'Entries', 'last_year': None, 'budget': 1000.0, 'actual': None, 'sort': 0},
                           {'section': 'Expense', 'description': 'Site', 'last_year': None, 'budget': 400.0, 'actual': None, 'sort': 1}]}, **kw)


@pytest.fixture
def budget(monkeypatch):
    state = {'b': _budget(), 'saved': [], 'log': [], 'draft': [], 'cancelled': []}
    monkeypatch.setattr(bud, '_guard', lambda: None)
    monkeypatch.setattr(bud, '_event', lambda code: {'event_name': 'Test Solo', 'date': '2027-05-01', 'program_code': 'AX'})
    monkeypatch.setattr(bud, 'load_events', lambda: [])
    monkeypatch.setattr(bud, 'load_programs', lambda: {'AX': 'Autocross'})
    monkeypatch.setattr(bud, '_plan_line', lambda code: None)
    monkeypatch.setattr(bud.F, 'enabled', lambda: False)
    monkeypatch.setattr(plan.O, 'all', lambda: [])
    monkeypatch.setattr(B, 'enabled', lambda: True)
    monkeypatch.setattr(B, 'get', lambda code: state['b'])
    monkeypatch.setattr(B, 'budgets', lambda: {'#C': state['b']})
    monkeypatch.setattr(B, 'qbo_actuals', lambda: {})
    monkeypatch.setattr(B, 'save', lambda header, lines, files=(), actuals_only=False: state['saved'].append((header, lines)))
    monkeypatch.setattr(B, 'set_draft', lambda code, d: state['draft'].append(d))
    monkeypatch.setattr(bud, '_cancel_ballots', lambda item, who, undecided_only=False: state['cancelled'].append(item) or True)
    monkeypatch.setattr(bud, 'log_audit_event', lambda action, who, d: state['log'].append((action, who)))
    return state


def test_ready_for_the_board_needs_the_right_to_change_the_budget(budget, monkeypatch):
    c = _client(monkeypatch, {'funds'})
    c.post('/events/event/%23C/ready')
    assert budget['draft'] == [] and "can't change this budget" in _flashes(c)
    c = _client(monkeypatch, {'events'})
    c.post('/events/event/%23C/ready')
    assert budget['draft'] == ['']


def _edit_form(site='400', who='Someone Else'):
    return MultiDict([('entered_by', who), ('chair', ''), ('location', ''), ('notes', ''),
                      ('inc_desc', 'Entries'), ('inc_ly', ''), ('inc_bud', '1000'), ('inc_act', ''),
                      ('exp_desc', 'Site'), ('exp_ly', ''), ('exp_bud', site), ('exp_act', ''),
                      ('exp_desc', 'Trophies'), ('exp_ly', ''), ('exp_bud', ''), ('exp_act', '')])


def test_budget_save_names_the_signed_in_person_and_skips_empty_starter_lines(budget, monkeypatch):
    c = _client(monkeypatch, {'events'})
    assert c.post('/events/event/%23C/edit', data=_edit_form()).status_code == 302
    header, lines = budget['saved'][0]
    assert header['updated_by'] == 'Pat Sample' and budget['log'] == [('BUDGET_SAVED', 'Pat Sample')]
    assert [ln['description'] for ln in lines] == ['Entries', 'Site']          # "Trophies" had no amounts
    assert budget['cancelled'] == []                                           # same numbers: the vote stands


def test_changing_the_numbers_cancels_the_open_email_vote(budget, monkeypatch):
    c = _client(monkeypatch, {'events'})
    c.post('/events/event/%23C/edit', data=_edit_form(site='900'))
    assert budget['cancelled'] == ['budget:2027:#C'] and ('BALLOT_CANCELLED', 'Pat Sample') in budget['log']
    assert 'email vote on this budget was cancelled' in _flashes(c)


def test_reported_results_land_on_their_own_line_when_the_budget_moved():
    lines = _budget()['lines']
    assert bud._pair_actuals(lines, ['950', '410'], []) == ([(lines[0], '950'), (lines[1], '410')], False)
    assert bud._pair_actuals(lines, ['950'], [])[1]                                       # a line was added meanwhile
    moved = [lines[1], dict(lines[0]), {'section': 'Expense', 'description': 'Timing', 'budget': 50.0, 'actual': 20.0, 'sort': 2}]
    pairs, stale = bud._pair_actuals(moved, ['950', '410'], ['Income|Entries', 'Expense|Site'])
    assert [(ln['description'], act) for ln, act in pairs] == [('Site', '410'), ('Entries', '950'), ('Timing', None)] and not stale
    assert bud._pair_actuals(lines[:1], ['950', '410'], ['Income|Entries', 'Expense|Site'])[1]    # typed for a line now gone
    assert not bud._pair_actuals(lines[:1], ['950', ''], ['Income|Entries', 'Expense|Site'])[1]   # nothing was typed for it


def test_a_bad_reported_amount_comes_back_with_the_rest_still_typed(budget, monkeypatch):
    c = _client(monkeypatch, {'events'})
    r = c.post('/events/event/%23C/actuals', data=MultiDict([('act', '950'), ('act', '4x0')]))
    html = r.get_data(as_text=True)
    assert r.status_code == 400 and not budget['saved']
    assert '&#34;4x0&#34; is not a number' in html and 'value="950.00"' in html


def test_use_that_date_works_for_an_event_found_by_its_code(monkeypatch, tmp_path):
    import storage
    import utils
    ev = {'unique_code': '#C', 'event_name': 'Test Solo', 'program_code': 'AX', 'date': '2027-05-01'}       # no pasted link
    saved = []
    monkeypatch.setattr(storage, 'DATA_LOCK_FILE', str(tmp_path / 'data.lock'))
    monkeypatch.setattr(bud, 'load_events', lambda: [ev])
    monkeypatch.setattr(utils, 'save_events', lambda evs: saved.append(evs[0]['date']))
    monkeypatch.setattr(bud.msr, 'link_for', lambda e, code, refresh=False: ('https://msr.example/e/1', 'name'))
    monkeypatch.setattr(bud.msr, 'cached', lambda link: {'date': '2027-05-08'} if link == 'https://msr.example/e/1' else None)
    monkeypatch.setattr(bud, 'log_audit_event', lambda *a, **k: None)
    c = _client(monkeypatch, {'events'})
    c.post('/events/event/%23C/msr-date')
    assert saved == ['2027-05-08'] and 'Date set to May 8, 2027' in _flashes(c)


def test_only_money_roles_can_hide_a_books_check_row(monkeypatch):
    marked = []
    monkeypatch.setattr(plan, '_guard', lambda: None)
    monkeypatch.setattr(plan.P, 'exceptions', lambda include_reviewed=False: [{'key': 'check:6080', 'kind': 'Check', 'detail': 'x'}])
    monkeypatch.setattr(plan.P, 'mark_reviewed', lambda key, who, note='': marked.append((key, who)))
    monkeypatch.setattr(plan, 'log_audit_event', lambda *a, **k: None)
    c = _client(monkeypatch, {'events', 'plan_own'})
    c.post('/plan/checks', data={'key': 'check:6080'})
    assert marked == [] and 'for the treasurer and the officers' in _flashes(c)
    c = _client(monkeypatch, {'pay'})
    c.post('/plan/checks', data={'key': 'check:6080', 'entered_by': 'Someone Else'})
    assert marked == [('check:6080', 'Pat Sample')]


def test_a_plan_line_with_a_bad_amount_stays_on_the_form(monkeypatch):
    saved = []
    monkeypatch.setattr(plan, '_guard', lambda: None)
    monkeypatch.setattr(plan, 'load_programs', lambda: {'AX': 'Autocross'})
    monkeypatch.setattr(plan, 'load_events', lambda: [])
    monkeypatch.setattr(plan.O, 'all', lambda: [])
    monkeypatch.setattr(plan.O, 'director', lambda p: ('Dana Director', 'ax@example.org'))
    monkeypatch.setattr(plan.B, 'enabled', lambda: False)
    monkeypatch.setattr(plan.P, 'plan', lambda y: {})
    monkeypatch.setattr(plan.P, 'actuals', lambda y: ({}, ''))
    monkeypatch.setattr(plan.P, 'exceptions', lambda include_reviewed=False: [])
    monkeypatch.setattr(plan.P, 'save', lambda *a, **k: saved.append(a))
    monkeypatch.setattr(plan, 'log_audit_event', lambda action, who, d: saved.append(who))
    c = _client(monkeypatch, {'plan_all'})
    form = MultiDict([('item', 'Spring Solo'), ('month', '5'), ('net', '14x0'), ('note', ''), ('code', ''),
                      ('item', 'Trophies'), ('month', ''), ('net', '-300'), ('note', ''), ('code', '')])
    r = c.post('/plan/2027/AX/edit', data=form)
    html = r.get_data(as_text=True)
    assert r.status_code == 400 and not saved
    assert 'value="Spring Solo"' in html and 'value="Trophies"' in html and '&#34;14x0&#34; must be a number' in html
    form.setlist('net', ['1400', '-300'])
    form.add('entered_by', 'Someone Else')
    assert c.post('/plan/2027/AX/edit', data=form).status_code == 302 and saved[-1] == 'Pat Sample'
