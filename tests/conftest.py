"""Test clients post like a real browser: with the session's forged-form token (see csrf.py)."""
import pytest
from flask.testing import FlaskClient

_open = FlaskClient.open


def _open_with_token(self, *args, **kw):
    method = (kw.get('method') or 'GET').upper()
    if method == 'POST' and not kw.pop('no_csrf', False) and 'json' not in kw and (kw.get('data') is None or isinstance(kw.get('data'), dict)):
        with self.session_transaction() as s:
            tok = s.setdefault('_csrf', 'test-token')
        kw['data'] = dict(kw.get('data') or {}, _csrf=tok)
    return _open(self, *args, **kw)


@pytest.fixture(autouse=True)
def _csrf_token_in_posts(monkeypatch):
    monkeypatch.setattr(FlaskClient, 'open', _open_with_token)
