"""Email groups shows the saved list of drscca.org groups. Made-up names only."""
import json

import pytest


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as A
    import groups
    import auth
    import officials
    f = tmp_path / 'email-groups.json'
    f.write_text(json.dumps({'updated': '2026-11-09T12:00-05:00', 'groups': [
        {'email': 'autocross@example.org', 'name': 'DRSCCA Autocross', 'aliases': [],
         'members': [{'email': 'boss@example.org', 'role': 'OWNER'}, {'email': 'pat@example.org', 'role': 'MANAGER'}]},
        {'email': 'legacy@example.org', 'name': 'DRSCCA Old addresses', 'aliases': ['old@example.org'], 'members': []}]}))
    monkeypatch.setattr(groups, 'GROUPS_FILE', str(f))
    monkeypatch.setattr(officials, 'all', lambda: [{'name': 'Pat Director', 'email': 'pat@example.org'}])
    monkeypatch.setattr(auth, 'volunteers', lambda: {})
    store = {'perms': {'admin'}, 'level': 'official'}
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Someone', 'level': store['level'], 'roles': [], 'perms': store['perms']})
    c = A.app.test_client()
    with c.session_transaction() as s:
        s['authenticated'] = True
        s['email'] = 'admin@example.org'
    c.store = store
    return c


def test_groups_table(client):
    html = client.get('/email-groups/').get_data(as_text=True)
    assert 'autocross@example.org' in html and 'Pat Director' in html and 'boss@example.org' not in html
    assert 'Also receives: old@example.org' in html and 'retired addresses' in html and 'Refresh from Google' in html


def test_a_volunteer_sees_the_list_but_cannot_refresh(client, tmp_path, monkeypatch):
    import groups
    monkeypatch.setattr(groups, 'GROUPS_REFRESH', str(tmp_path / 'groups-refresh'))
    client.store.update(perms={'invoices'}, level='volunteer')
    html = client.get('/email-groups/').get_data(as_text=True)
    assert 'autocross@example.org' in html and 'Refresh from Google' not in html
    client.post('/email-groups/refresh')
    assert not (tmp_path / 'groups-refresh').exists()


def test_refresh_asks_the_server(client, tmp_path, monkeypatch):
    import groups
    monkeypatch.setattr(groups, 'GROUPS_REFRESH', str(tmp_path / 'groups-refresh'))
    assert client.post('/email-groups/refresh').status_code == 302
    assert 'Refreshing' in client.get('/email-groups/').get_data(as_text=True)
