"""Email-code sign-in: who gets in, at what level, the code rules, and what a volunteer can open."""
import pytest

OFFICIALS = [
    {'role': 'Treasurer', 'name': 'Pat Treasurer', 'email': 'treasurer@example.org'},
    {'role': 'Director', 'name': 'Rae Director', 'email': 'rx@example.org', 'program': 'RX'},
    {'role': 'Equipment', 'name': 'Ed Gear', 'email': 'ed@example.org'},
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
    from app import app
    app.config['TESTING'] = True
    app.mail = mail
    return app


def _code(mail):
    return mail[-1][0].split()[0]


def test_levels(app_):
    import auth
    assert auth.lookup('TREASURER@example.org ')['level'] == 'officer'
    assert auth.lookup('rx@example.org')['level'] == 'official'
    assert auth.lookup('vol@example.org')['level'] == 'volunteer'
    assert auth.lookup('stranger@example.org') is None


def test_unknown_address_gets_no_code_and_same_answer(app_):
    c = app_.test_client()
    r = c.post('/signin', data={'email': 'stranger@example.org'})
    assert r.status_code == 302 and '/signin/code' in r.location
    assert app_.mail == []


def test_code_signs_in_once(app_):
    c = app_.test_client()
    c.post('/signin', data={'email': 'rx@example.org'})
    code = _code(app_.mail)
    assert len(code) == 6 and code.isdigit()
    r = c.post('/signin/code', data={'email': 'rx@example.org', 'code': code})
    assert r.status_code == 302 and r.location.endswith('/')
    with c.session_transaction() as s:
        assert s['email'] == 'rx@example.org'
    # used up: a second device cannot reuse it
    c2 = app_.test_client()
    c2.post('/signin/code', data={'email': 'rx@example.org', 'code': code})
    with c2.session_transaction() as s:
        assert 'email' not in s


def test_wrong_codes_kill_the_code(app_):
    import auth
    c = app_.test_client()
    c.post('/signin', data={'email': 'rx@example.org'})
    code = _code(app_.mail)
    wrong = '000000' if code != '000000' else '111111'
    for _ in range(auth.MAX_TRIES):
        c.post('/signin/code', data={'email': 'rx@example.org', 'code': wrong})
    c.post('/signin/code', data={'email': 'rx@example.org', 'code': code})
    with c.session_transaction() as s:
        assert 'email' not in s


def test_send_limit(app_):
    import auth
    c = app_.test_client()
    for _ in range(auth.MAX_SENDS_PER_HOUR + 2):
        c.post('/signin', data={'email': 'rx@example.org'})
    assert len(app_.mail) == auth.MAX_SENDS_PER_HOUR


def test_emailed_link_only_fills_the_form(app_):
    c = app_.test_client()
    c.post('/signin', data={'email': 'rx@example.org'})
    code = _code(app_.mail)
    r = c.get(f'/signin/code?e=rx@example.org&c={code}')
    assert r.status_code == 200 and code.encode() in r.data
    with c.session_transaction() as s:
        assert 'email' not in s       # a mail scanner opening the link does not sign anyone in


def _signed_in(app_, email):
    c = app_.test_client()
    with c.session_transaction() as s:
        s['email'] = email
    return c


def test_roles_set_officer_and_editor(app_):
    c = _signed_in(app_, 'treasurer@example.org')
    c.get('/officials')
    with c.session_transaction() as s:
        assert s['who'] == 'Pat Treasurer' and s['officer']['email'] == 'treasurer@example.org' and s['equip_editor']
    c = _signed_in(app_, 'rx@example.org')
    with c.session_transaction() as s:
        s['officer'] = {'email': 'rx@example.org', 'name': 'forged'}
    c.get('/officials')
    with c.session_transaction() as s:
        assert 'officer' not in s and 'equip_editor' not in s


def test_volunteer_kept_in_lane(app_):
    c = _signed_in(app_, 'vol@example.org')
    for path in ('/treasurer', '/audit', '/officials', '/plan/', '/operations/', '/events/new', '/access'):
        r = c.get(path)
        assert r.status_code == 302 and r.location.endswith('/'), path


def test_removed_person_is_signed_out(app_):
    c = _signed_in(app_, 'gone@example.org')
    c.get('/officials')
    with c.session_transaction() as s:
        assert 'email' not in s and not s.get('authenticated')


def test_old_password_session_is_dropped(app_):
    c = app_.test_client()
    with c.session_transaction() as s:
        s['authenticated'] = True
        s['who'] = 'Anyone'
    r = c.get('/officials')
    assert r.status_code == 302 and '/?next=' in r.location
