from app import app


def test_google_button_hidden_without_client(monkeypatch):
    client = app.test_client()
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_ID', raising=False)
    r = client.get('/signin/google')
    assert r.status_code == 302 and 'accounts.google.com' not in r.headers['Location']


def test_google_redirect_and_bad_state(monkeypatch):
    client = app.test_client()
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_ID', 'cid.apps.googleusercontent.com')
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_SECRET', 'x')
    r = client.get('/signin/google?next=/funds/')
    loc = r.headers['Location']
    assert loc.startswith('https://accounts.google.com/') and 'scope=openid+email' in loc and 'signin%2Fgoogle%2Fback' in loc
    r = client.get('/signin/google/back?state=wrong&code=abc')
    assert r.status_code == 302      # refused, back to the sign-in page
