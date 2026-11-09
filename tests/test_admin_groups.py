"""Admin > Email groups shows the saved list of drscca.org groups. Made-up names only."""
import json

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as A
    import admin
    import auth
    import officials
    f = tmp_path / 'email-groups.json'
    f.write_text(json.dumps({'updated': '2026-11-09T12:00-05:00', 'groups': [
        {'email': 'autocross@example.org', 'name': 'DRSCCA Autocross', 'aliases': [],
         'members': [{'email': 'boss@example.org', 'role': 'OWNER'}, {'email': 'pat@example.org', 'role': 'MANAGER'}]},
        {'email': 'legacy@example.org', 'name': 'DRSCCA Old addresses', 'aliases': ['old@example.org'], 'members': []}]}))
    monkeypatch.setattr(admin, 'GROUPS_FILE', str(f))
    monkeypatch.setattr(officials, 'all', lambda: [{'name': 'Pat Director', 'email': 'pat@example.org'}])
    monkeypatch.setattr(auth, 'volunteers', lambda: {})
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Admin', 'level': 'official', 'roles': [], 'perms': {'admin'}})
    c = A.app.test_client()
    with c.session_transaction() as s:
        s['authenticated'] = True
        s['email'] = 'admin@example.org'
    return c


def test_groups_table(client):
    html = client.get('/admin/groups').get_data(as_text=True)
    assert 'autocross@example.org' in html and 'Pat Director' in html and 'boss@example.org' not in html
    assert 'Also receives: old@example.org' in html and 'retired addresses' in html and 'set in Google' in html


def test_refresh_asks_the_server(client, tmp_path, monkeypatch):
    import admin
    monkeypatch.setattr(admin, 'GROUPS_REFRESH', str(tmp_path / 'groups-refresh'))
    assert client.post('/admin/groups').status_code == 302
    assert 'Refreshing' in client.get('/admin/groups').get_data(as_text=True)


def test_synced_groups_match_the_handover_tool():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
    import admin
    import handover
    assert admin.SYNCED_GROUPS == set(handover.wanted([]))
