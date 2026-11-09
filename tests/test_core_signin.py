"""Sign-in code limits, the volunteers list during a Sheets outage, and who may add files to a money-received report."""
import pytest

OFFICIALS = [
    {'role': 'Treasurer', 'name': 'Pat Treasurer', 'email': 'treasurer@example.org'},
    {'role': 'Director', 'name': 'Rae Director', 'email': 'rx@example.org', 'program': 'RX'},
]


@pytest.fixture()
def app_(monkeypatch, tmp_path):
    import auth
    import officials
    monkeypatch.setattr(officials, 'all', lambda: [dict(o) for o in OFFICIALS])
    monkeypatch.setattr(auth, 'volunteers', lambda: {'vol@example.org': {'email': 'vol@example.org', 'name': 'Val Volunteer'}})
    monkeypatch.setattr(auth, 'CODES_FILE', str(tmp_path / 'codes.json'))
    mail = []
    monkeypatch.setattr(auth, 'send_mail', lambda subject, body, to=None, **kw: mail.append((subject, body, to)))
    monkeypatch.setattr(auth, 'log_audit_event', lambda *a, **kw: None)
    monkeypatch.setattr(auth, '_later', lambda fn: fn())
    from app import app
    app.config['TESTING'] = True
    app.mail = mail
    return app


def _code(mail):
    return mail[-1][0].split()[0]


def _wrong(code):
    return '000000' if code != '000000' else '111111'


def _signed_in(c):
    with c.session_transaction() as s:
        return 'email' in s


def test_a_new_code_does_not_buy_more_guesses(app_):
    import auth
    c = app_.test_client()
    for _ in range(2):                                   # two codes, five wrong guesses each: the hourly limit
        c.post('/signin', data={'email': 'rx@example.org'})
        for _ in range(auth.MAX_TRIES):
            c.post('/signin/code', data={'email': 'rx@example.org', 'code': _wrong(_code(app_.mail))})
    c.post('/signin', data={'email': 'rx@example.org'})   # a third code, and this time the right one
    c.post('/signin/code', data={'email': 'rx@example.org', 'code': _code(app_.mail)})
    assert not _signed_in(c)


def test_wrong_guesses_are_forgotten_after_signing_in(app_):
    import auth
    c = app_.test_client()
    c.post('/signin', data={'email': 'rx@example.org'})
    c.post('/signin/code', data={'email': 'rx@example.org', 'code': _wrong(_code(app_.mail))})
    c.post('/signin/code', data={'email': 'rx@example.org', 'code': _code(app_.mail)})
    assert _signed_in(c)
    assert auth._codes(lambda codes, now: codes['rx@example.org']['bad']) == []


def test_daily_send_limit(app_, monkeypatch):
    import auth
    c = app_.test_client()
    clock = [1_000_000.0]
    monkeypatch.setattr(auth.time, 'time', lambda: clock[0])
    for _ in range(auth.MAX_SENDS_PER_DAY + 5):          # one every 20 minutes: never over the hourly limit
        c.post('/signin', data={'email': 'rx@example.org'})
        clock[0] += 1200
    assert len(app_.mail) == auth.MAX_SENDS_PER_DAY


def test_one_network_address_can_only_ask_for_so_many_codes(app_, monkeypatch):
    import auth
    monkeypatch.setattr(auth, 'IP_SENDS_PER_HOUR', 3)
    c = app_.test_client()
    for who in ('rx@example.org', 'treasurer@example.org', 'vol@example.org', 'rx@example.org'):
        r = c.post('/signin', data={'email': who})
    assert len(app_.mail) == 3 and r.location.endswith('/')
    # another network address is not held back by the first
    c.post('/signin', data={'email': 'rx@example.org'}, environ_base={'REMOTE_ADDR': '203.0.113.9'})
    assert len(app_.mail) == 4


def test_one_network_address_can_only_guess_so_many_times(app_, monkeypatch):
    import auth
    monkeypatch.setattr(auth, 'IP_WRONG_PER_HOUR', 3)
    c = app_.test_client()
    for n in range(3):                                   # wrong codes for addresses on no list count too
        c.post('/signin/code', data={'email': f'stranger{n}@example.org', 'code': '123456'})
    c.post('/signin', data={'email': 'rx@example.org'})
    c.post('/signin/code', data={'email': 'rx@example.org', 'code': _code(app_.mail)})
    assert not _signed_in(c)
    c.post('/signin/code', data={'email': 'rx@example.org', 'code': _code(app_.mail)}, environ_base={'REMOTE_ADDR': '203.0.113.9'})
    assert _signed_in(c)


def test_the_visitors_address_comes_from_the_proxy(app_):
    with app_.test_client() as c:
        c.get('/privacy', headers={'X-Forwarded-For': '198.51.100.7'})
        from flask import request
        assert request.remote_addr == '198.51.100.7'


def test_listed_and_unlisted_addresses_get_the_same_answer(app_):
    c = app_.test_client()
    a = c.post('/signin', data={'email': 'rx@example.org'})
    b = c.post('/signin', data={'email': 'stranger@example.org'})
    assert (a.status_code, a.location) == (b.status_code, b.location)
    a = c.post('/signin/code', data={'email': 'rx@example.org', 'code': _wrong(_code(app_.mail))}, follow_redirects=True)
    b = c.post('/signin/code', data={'email': 'stranger@example.org', 'code': '123456'}, follow_redirects=True)
    assert b'That code is not right' in a.data and b'That code is not right' in b.data


def test_the_code_is_sent_off_the_request(app_, monkeypatch):
    import auth
    held = []
    monkeypatch.setattr(auth, '_later', held.append)
    r = app_.test_client().post('/signin', data={'email': 'rx@example.org'})
    assert r.status_code == 302 and app_.mail == [] and len(held) == 1
    held[0]()
    assert len(app_.mail) == 1 and '/signin/code?e=rx@example.org&c=' + _code(app_.mail) in app_.mail[0][1]


def test_next_refuses_a_tab(app_):
    from utils import local_path
    for bad in ('/\t/evil.example', '/\n/evil.example', '/\x0b/evil.example', '/ok\x7f'):
        assert local_path(bad) == ''
    assert local_path('/funds/FR-1?x=1') == '/funds/FR-1?x=1'


# ---------- the volunteers list when Sheets is down ----------

class _Store:
    sheet_id = 's1'

    def __init__(self):
        self.rows, self.fail, self.loads = [{'email': 'Vol@Example.org', 'name': 'Val Volunteer'}], None, 0

    def _records(self, rng):
        if self.fail:
            raise self.fail
        return self.rows

    def _cached(self, key, loader):
        self.loads += 1
        return loader()


def test_a_failed_volunteers_read_keeps_the_last_good_list(monkeypatch):
    import auth
    st = _Store()
    monkeypatch.setattr(auth, 'store', st)
    monkeypatch.setattr(auth, '_vols', {'good': None, 'failed': False})
    assert list(auth.volunteers()) == ['vol@example.org']
    st.fail = RuntimeError('Sheets is down')
    assert list(auth.volunteers()) == ['vol@example.org'] and not auth.volunteers_unknown()
    st.fail, st.rows = None, []
    assert auth.volunteers() == {}                       # a real, empty list is believed


def test_nobody_is_signed_out_while_the_list_cannot_be_read(monkeypatch):
    import auth
    import officials
    st = _Store()
    st.fail = RuntimeError('Sheets is down')
    monkeypatch.setattr(auth, 'store', st)
    monkeypatch.setattr(auth, '_vols', {'good': None, 'failed': False})
    monkeypatch.setattr(officials, 'all', lambda: [dict(o) for o in OFFICIALS])
    from app import app
    app.config['TESTING'] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s['email'] = 'vol@example.org'
    r = c.get('/funds/')
    assert r.status_code == 503 and b'Reload' in r.data
    with c.session_transaction() as s:
        assert s['email'] == 'vol@example.org'


# ---------- money received: files only on your own report ----------

def test_a_limited_person_cannot_add_files_to_someone_elses_report(app_, monkeypatch):
    import fr
    import received_store
    reports = {'CR-1': {'id': 'CR-1', 'reported_email': 'vol@example.org', 'reported_by': 'Val Volunteer', 'status': 'Received'},
               'CR-2': {'id': 'CR-2', 'reported_email': 'rx@example.org', 'reported_by': 'Rae Director', 'status': 'Received'}}
    monkeypatch.setattr(received_store, 'get', lambda rid: reports.get(rid))
    monkeypatch.setattr(received_store, 'refresh', lambda: None)
    monkeypatch.setattr(fr.F, 'enabled', lambda: True)
    added = []
    monkeypatch.setattr(received_store, 'add_files', lambda rid, files: added.append(rid) or 0)
    c = app_.test_client()
    with c.session_transaction() as s:
        s['email'] = 'vol@example.org'
    assert c.post('/funds/in/CR-2/files').status_code == 403 and added == []
    assert c.post('/funds/in/CR-1/files').status_code == 302 and added == ['CR-1']
