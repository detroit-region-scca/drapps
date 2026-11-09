"""An API key is its owner: the same permissions, the same "own requests only", the same approvals."""
import pytest

KEYS = {'treasurer@example.org': 't' * 40, 'rx@example.org': 'r' * 40, 're@example.org': 'e' * 40, 'vol@example.org': 'v' * 40}
OFFICIALS = [
    {'role': 'Treasurer', 'name': 'Pat Treasurer', 'email': 'treasurer@example.org'},
    {'role': 'Director', 'name': 'Rae Director', 'email': 'rx@example.org', 'program': 'RX'},
    {'role': 'RE', 'name': 'Remy Exec', 'email': 're@example.org'},
]
REQS = [
    {'id': 'FR-1', 'status': 'Submitted', 'program': 'RX', 'event_code': '', 'submitted': '2026-10-01', 'net': 50.0,
     'submitter_email': 'rx@example.org', 'entered_by': 'Rae Director', 'approvals': ''},
    {'id': 'FR-2', 'status': 'Submitted', 'program': 'AX', 'event_code': '', 'submitted': '2026-10-02', 'net': 75.0,
     'submitter_email': 'someone@example.org', 'entered_by': 'Sam Other', 'approvals': ''},
]


class FakeApprovals:
    def __init__(self):
        self.reqs = [dict(r) for r in REQS]

    def items(self, r):
        return []


def _setup(monkeypatch):
    monkeypatch.setenv('API_KEYS', ','.join(f'{e}:{k}' for e, k in KEYS.items()))
    import access
    import api
    import auth
    import fr
    import officials
    monkeypatch.setattr(officials, 'all', lambda: [dict(o) for o in OFFICIALS])
    monkeypatch.setattr(auth, 'volunteers', lambda: {'vol@example.org': {'email': 'vol@example.org', 'name': 'Val Volunteer'}})
    monkeypatch.setattr(fr, 'Approvals', FakeApprovals)
    monkeypatch.setattr(api.F, 'get', lambda i: next((dict(r) for r in REQS if r['id'] == i), None))
    monkeypatch.setattr(api.F, 'enabled', lambda: True)
    # directors here see only their own requests and write only their own program's plan
    access.save('Director', (access.EVERYDAY_DEFAULT - {'funds_all', 'invoices_all', 'plan_all'}) | {'plan_own'})
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


@pytest.fixture()
def client(monkeypatch):
    """Writes stop at the hand-off to the web form."""
    import api
    c = _setup(monkeypatch)
    c.sent = []
    monkeypatch.setattr(api, '_forward', lambda path, form, files=(): (c.sent.append(path), (302, '', []))[1])
    return c


def key(email):
    return {'Authorization': f'Bearer {KEYS[email]}'}


def test_a_volunteers_address_gets_no_api(client):
    assert client.get('/api/v1/', headers=key('vol@example.org')).status_code == 401


def test_key_carries_its_owners_session_not_an_officers(client):
    client.get('/api/v1/', headers=key('rx@example.org'))
    with client.session_transaction() as s:
        assert s['email'] == 'rx@example.org' and 'officer' not in s and 'equip_editor' not in s
    client.get('/api/v1/', headers=key('treasurer@example.org'))
    with client.session_transaction() as s:
        assert s['officer']['email'] == 'treasurer@example.org'


def test_limited_owner_lists_only_their_own_requests(client):
    assert [r['id'] for r in client.get('/api/v1/funds', headers=key('rx@example.org')).json] == ['FR-1']
    assert client.get('/api/v1/funds/FR-2', headers=key('rx@example.org')).status_code == 404
    assert [r['id'] for r in client.get('/api/v1/funds', headers=key('treasurer@example.org')).json] == ['FR-1', 'FR-2']


def test_audit_needs_the_permission_and_survives_a_bad_limit(client, monkeypatch):
    import api
    monkeypatch.setattr(api, 'load_audit_logs', lambda: [{'timestamp': f'2026-11-0{n}'} for n in (9, 8, 7)])   # newest first
    assert client.get('/api/v1/audit', headers=key('rx@example.org')).status_code == 403
    r = client.get('/api/v1/audit?limit=2', headers=key('treasurer@example.org'))
    assert [e['timestamp'] for e in r.json] == ['2026-11-09', '2026-11-08']
    for bad in ('abc', '-5', '0', ''):
        assert client.get(f'/api/v1/audit?limit={bad}', headers=key('treasurer@example.org')).status_code == 200


def test_chair_approval_only_from_that_programs_director(client):
    r = client.post('/api/v1/funds/FR-2/approve', json={'role': 'chair'}, headers=key('rx@example.org'))     # an AX request
    assert r.status_code == 403 and not client.sent
    r = client.post('/api/v1/funds/FR-1/approve', json={'role': 'chair'}, headers=key('treasurer@example.org'))
    assert r.status_code == 403 and not client.sent
    r = client.post('/api/v1/funds/FR-1/approve', json={'role': 'chair'}, headers=key('rx@example.org'))
    assert r.status_code == 200 and client.sent[-1].startswith('/funds/approve/')


def test_second_signer_only_from_the_re_or_are(client):
    assert client.post('/api/v1/funds/FR-1/approve', json={'role': 'second'}, headers=key('treasurer@example.org')).status_code == 403
    assert client.post('/api/v1/funds/FR-1/approve', json={'role': 'second'}, headers=key('re@example.org')).status_code == 200
    assert client.post('/api/v1/funds/FR-1/approve', json={'role': 'board'}, headers=key('re@example.org')).status_code == 400
    assert len(client.sent) == 1


def test_web_pages_refuse_a_key_whose_owner_lacks_the_permission(monkeypatch):
    """The real hand-off this time: a director's key can't reject a request, the treasurer's key can."""
    import api
    import fr
    client = _setup(monkeypatch)
    monkeypatch.setattr(api.F, 'refresh', lambda: None)
    closed = []
    monkeypatch.setattr(api.F, 'close', lambda rid, status, **kw: closed.append((rid, status)))
    monkeypatch.setattr(fr, 'log_audit_event', lambda *a, **kw: None)
    monkeypatch.setattr(fr, 'tell_submitter', lambda rid: None)
    r = client.post('/api/v1/funds/FR-1/reject', json={'note': 'no'}, headers=key('rx@example.org'))
    assert r.status_code == 400 and not closed and 'Marking payments' in r.json['errors'][0]
    r = client.post('/api/v1/funds/FR-1/reject', json={'note': 'no'}, headers=key('treasurer@example.org'))
    assert r.status_code == 200 and closed == [('FR-1', 'Rejected')]


def test_operations_and_plan_writes_need_the_owners_permission(client, monkeypatch):
    import api
    import equip_store as E
    monkeypatch.setattr(E, 'enabled', lambda: True)
    monkeypatch.setattr(E, 'tasks', lambda: [{'id': 'TK-1', 'task': 'Renew', 'due': '2027-01-31'}])
    monkeypatch.setattr(api.P, 'plan', lambda y: {})
    assert client.post('/api/v1/operations/tasks/TK-1/done', headers=key('rx@example.org')).status_code == 403
    assert client.put('/api/v1/plans/2027/AX', json={'lines': []}, headers=key('rx@example.org')).status_code == 403
    assert not client.sent
    assert client.post('/api/v1/operations/tasks/TK-1/done', headers=key('treasurer@example.org')).status_code == 200
    client.put('/api/v1/plans/2027/AX', json={'lines': []}, headers=key('treasurer@example.org'))
    client.put('/api/v1/plans/2027/RX', json={'lines': []}, headers=key('rx@example.org'))        # their own program
    assert client.sent == ['/operations/tasks/TK-1', '/plan/2027/AX/edit', '/plan/2027/RX/edit']
