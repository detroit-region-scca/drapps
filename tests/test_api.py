"""The JSON API: key check, and that each write hands the right form to the app's own page (no Sheets, no email)."""
import base64

import pytest

KEY = 'k' * 40


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv('API_KEYS', f'treasurer@example.org:{KEY}')
    import api
    import officials
    monkeypatch.setattr(officials, 'all', lambda: [{'role': 'Treasurer', 'name': 'Pat Treasurer', 'email': 'treasurer@example.org'}])
    sent = []

    def fake_forward(path, form, files=()):
        sent.append({'path': path, 'form': form, 'files': files})
        return 302, '', []
    monkeypatch.setattr(api, '_forward', fake_forward)
    for mod, name, val in ((api.B, 'get', lambda c: None), (api.P, 'plan', lambda y: {}), (api.F, 'get', lambda i: None),
                           (api.I, 'get', lambda i: None), (api, 'load_events', lambda: [])):
        monkeypatch.setattr(mod, name, val)
    from app import app
    app.config['TESTING'] = True
    c = app.test_client()
    c.sent = sent
    return c


def auth(key=KEY):
    return {'Authorization': f'Bearer {key}'}


def test_no_key_or_wrong_key_is_refused(client):
    assert client.get('/api/v1/').status_code == 401
    assert client.get('/api/v1/', headers=auth('x' * 40)).status_code == 401


def test_key_acts_as_the_official(client):
    r = client.get('/api/v1/', headers=auth())
    assert r.status_code == 200 and r.json['user'] == {'email': 'treasurer@example.org', 'name': 'Pat Treasurer'}


def test_short_keys_are_ignored(monkeypatch):
    monkeypatch.setenv('API_KEYS', 'a@b.org:short,c@d.org:' + 'z' * 32)
    import api
    assert list(api._keys().values()) == ['c@d.org']


def test_funds_request_becomes_the_web_form(client):
    body = {'program': 'OPS', 'method': 'card', 'notes': 'n',
            'lines': [{'date': '2026-10-31', 'description': 'Plan', 'category': 'Other', 'amount': 300}],
            'payee': {'name': 'WordPress.com'},
            'receipts': [{'filename': 'r.pdf', 'content_base64': base64.b64encode(b'%PDF').decode(), 'content_type': 'application/pdf'}]}
    client.post('/api/v1/funds', json=body, headers=auth())
    s = client.sent[-1]
    assert s['path'] == '/funds/new'
    f = s['form']
    assert f['program'] == 'OPS' and f['method'] == 'card' and f['line_amount'] == [300] and f['line_desc'] == ['Plan']
    assert f['payee_name'] == 'WordPress.com' and f['submitter_name'] == 'Pat Treasurer' and f['submitter_email'] == 'treasurer@example.org'
    assert s['files'] == [('receipts', 'r.pdf', b'%PDF', 'application/pdf')]


def test_bad_receipt_is_refused_before_anything_is_sent(client):
    r = client.post('/api/v1/funds', json={'receipts': [{'filename': 'r.pdf', 'content_base64': '***'}]}, headers=auth())
    assert r.status_code == 400 and not client.sent


def test_budget_lines_split_by_section(client):
    client.put('/api/v1/budgets/%23DRABC', json={'lines': [{'section': 'Income', 'description': 'Entries', 'budget': 900},
                                                           {'section': 'Expense', 'description': 'Site', 'budget': 400, 'last_year': 350}]},
               headers=auth())
    s = client.sent[-1]
    assert s['path'] == '/events/event/%23DRABC/edit'
    assert s['form']['inc_desc'] == ['Entries'] and s['form']['exp_bud'] == [400] and s['form']['exp_ly'] == [350]


def test_plan_save_and_submit(client):
    client.put('/api/v1/plans/2027/RY', json={'lines': [{'item': 'June rally', 'month': 6, 'net': 300}], 'submit': True}, headers=auth())
    s = client.sent[-1]
    assert s['path'] == '/plan/2027/RY/edit' and s['form']['action'] == 'submit' and s['form']['month'] == [6] and s['form']['code'] == ['']


def test_invoice_lines(client):
    client.post('/api/v1/invoices', json={'program': 'DR', 'bill_name': 'Sponsor', 'lines': [{'description': 'Banner', 'quantity': 2, 'unit_price': 50}]},
                headers=auth())
    s = client.sent[-1]
    assert s['path'] == '/invoices/new' and s['form']['qty'] == [2] and s['form']['price'] == [50] and s['form']['bill_name'] == 'Sponsor'


def test_approve_role_is_checked(client):
    r = client.post('/api/v1/funds/FR-1/approve', json={'role': 'king'}, headers=auth())
    assert r.status_code == 400 and not client.sent


def test_plan_permission_lets_an_api_key_through(monkeypatch):
    import auth
    import officials
    import plan
    from flask import g
    from app import app
    monkeypatch.setattr(officials, 'all', lambda: [{'role': 'Director', 'program': 'RY', 'name': 'R', 'email': 'r@x.com'}])
    with app.test_request_context('/'):
        g.api_user = {'email': 'treasurer@example.org', 'name': 'Pat'}
        assert plan.may_write('AX')
    with app.test_request_context('/'):
        monkeypatch.setattr(auth, 'user', lambda: {'email': 'r@x.com', 'perms': ['plan_own']})
        assert plan.may_write('RY') and not plan.may_write('AX')


@pytest.fixture()
def ops(client, monkeypatch):
    import equip_store as E
    monkeypatch.setattr(E, 'enabled', lambda: True)
    monkeypatch.setattr(E, 'tasks', lambda: [{'id': 'TK-1', 'task': 'Renew', 'due': '2027-01-31'}])
    return client


def test_task_update_sends_only_given_fields(ops):
    r = ops.put('/api/v1/operations/tasks/TK-1', json={'due': '2027-02-01', 'cost': 410, 'bogus': 'x'}, headers=auth())
    s = ops.sent[-1]
    assert r.status_code == 200 and s['path'] == '/operations/tasks/TK-1' and s['form'] == {'due': '2027-02-01', 'cost': 410}


def test_task_done_and_unknown_task(ops):
    ops.post('/api/v1/operations/tasks/TK-1/done', headers=auth())
    assert ops.sent[-1] == {'path': '/operations/tasks/TK-1', 'form': {'action': 'done'}, 'files': ()}
    assert ops.post('/api/v1/operations/tasks/TK-9/done', headers=auth()).status_code == 404


def test_worker_points_become_the_enter_form(client):
    client.post('/api/v1/members/points', json={'event_code': '', 'what': 'Timing trailer work', 'date': '2026-11-01',
                                               'entries': [{'member': '123', 'points': 2}, {'member': 'Pat Example'}]}, headers=auth())
    s = client.sent[-1]
    assert s['path'] == '/members/enter' and s['form']['event'] == 'none' and s['form']['member'] == ['123', 'Pat Example']
    assert s['form']['points'] == [2, 1] and s['form']['first'] == '2026-11-01'


def test_board_ask_and_record_tick_the_items(client):
    items = {'budgets': [{'year': '2027', 'code': '#DRABC'}], 'motions': ['MO-2026-01']}
    client.post('/api/v1/board/ask', json=dict(items, urgent=True), headers=auth())
    s = client.sent[-1]
    assert s['path'] == '/board/' and s['form']['item'] == ['budget:2027:#DRABC', 'motion:MO-2026-01'] and s['form']['urgent'] == 'yes'
    client.post('/api/v1/board/record', json=dict(items, result='passed', date='2026-11-12', how='meeting', vote='7-0'), headers=auth())
    f = client.sent[-1]['form']
    assert f['result'] == 'passed' and f['decided'] == '2026-11-12' and f['how'] == 'meeting' and f['vote'] == '7-0'


def test_volunteers_add_and_remove(client):
    client.post('/api/v1/volunteers', json={'name': 'Val', 'email': 'val@example.org'}, headers=auth())
    assert client.sent[-1]['path'] == '/access' and client.sent[-1]['form']['email'] == 'val@example.org'
    client.delete('/api/v1/volunteers/val@example.org', headers=auth())
    assert client.sent[-1]['form'] == {'action': 'remove', 'email': 'val@example.org'}
