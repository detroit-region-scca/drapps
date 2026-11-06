"""Test clients post like a real browser: with the session's forged-form token (see csrf.py)."""
import pytest
from flask.testing import FlaskClient
from werkzeug.datastructures import MultiDict

_open = FlaskClient.open


def _open_with_token(self, *args, **kw):
    method = (kw.get('method') or 'GET').upper()
    if method == 'POST' and not kw.pop('no_csrf', False) and 'json' not in kw and (kw.get('data') is None or isinstance(kw.get('data'), dict)):
        with self.session_transaction() as s:
            tok = s.setdefault('_csrf', 'test-token')
        data = kw.get('data')
        if isinstance(data, MultiDict):       # keep repeated fields (several lines): dict() would keep only the first
            data = data.copy()
            data.add('_csrf', tok)
            kw['data'] = data
        else:
            kw['data'] = dict(data or {}, _csrf=tok)
    return _open(self, *args, **kw)


@pytest.fixture(autouse=True)
def _csrf_token_in_posts(monkeypatch):
    monkeypatch.setattr(FlaskClient, 'open', _open_with_token)


@pytest.fixture(autouse=True)
def _role_access_in_tmp(tmp_path, monkeypatch):
    """Tests never read or write the server's data/role-access.json."""
    import access
    monkeypatch.setattr(access, 'PATH', str(tmp_path / 'role-access.json'))


@pytest.fixture(autouse=True)
def _short_links_in_tmp(tmp_path, monkeypatch):
    """Tests never read or write the server's data/short_links.json."""
    import shortlinks
    monkeypatch.setattr(shortlinks, 'PATH', str(tmp_path / 'short_links.json'))
    monkeypatch.setattr(shortlinks, 'LOCK', str(tmp_path / 'short_links.json.lock'))


@pytest.fixture(autouse=True)
def _audit_log_in_tmp(tmp_path, monkeypatch):
    """Tests never append to the server's data/audit.ndjson (they used to: 900+ fake entries by 'Someone')."""
    import storage
    monkeypatch.setattr(storage, 'AUDIT_FILE', str(tmp_path / 'audit.ndjson'))
