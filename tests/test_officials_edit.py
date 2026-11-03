import json

import utils
from app import app


def _as(c, monkeypatch, roles):
    import auth
    with c.session_transaction() as s:
        s['email'] = 'x@example.org'
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Someone', 'level': 'officer', 'roles': roles} if e else None)


def test_only_admin_can_queue_a_change(tmp_path, monkeypatch):
    import officials
    monkeypatch.setattr(utils, 'OFFICIALS_QUEUE', str(tmp_path / 'q.json'))
    monkeypatch.setattr(utils, 'OFFICIALS_DONE', str(tmp_path / 'd.json'))
    monkeypatch.setattr(officials, 'all', lambda: [{'role': 'Director', 'title': 'Autocross Director', 'name': 'Old', 'email': 'old@x.com'}])
    c = app.test_client()
    _as(c, monkeypatch, ['Treasurer'])
    c.post('/officials', data={'title': 'Autocross Director', 'from': 'old@x.com', 'name': 'New', 'email': 'new@x.com'})
    assert not (tmp_path / 'q.json').exists()
    _as(c, monkeypatch, ['Admin'])
    c.post('/officials', data={'title': 'Autocross Director', 'from': 'old@x.com', 'name': 'New Person', 'email': 'new@x.com'})
    q = json.loads((tmp_path / 'q.json').read_text())['changes']
    assert q[0]['title'] == 'Autocross Director' and q[0]['email'] == 'new@x.com' and q[0]['from'] == 'old@x.com'
