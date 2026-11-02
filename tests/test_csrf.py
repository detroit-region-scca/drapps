from app import app


def test_post_without_token_is_refused_and_forms_get_one():
    c = app.test_client()
    r = c.post('/signin', data={'email': 'someone@example.org'}, no_csrf=True)
    assert r.status_code == 302
    with c.session_transaction() as s:
        assert '_flashes' in s and 'expired' in s['_flashes'][-1][1]
    html = c.get('/').get_data(as_text=True)
    if 'method="post"' in html.lower() or "method='post'" in html.lower():
        assert 'name="_csrf"' in html


def test_post_with_token_goes_through():
    c = app.test_client()
    r = c.post('/signin', data={'email': 'someone@example.org'})
    with c.session_transaction() as s:
        assert not any('expired' in m for _, m in s.get('_flashes', []))


def test_local_path_refuses_other_sites():
    from utils import local_path
    assert local_path('/funds/1') == '/funds/1'
    for bad in ('//evil.example', '/\\evil.example', 'https://evil.example', '/x\nSet-Cookie: a', None, ''):
        assert local_path(bad) == ''
