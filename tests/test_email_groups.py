"""Email groups shows the saved list of drscca.org groups. Made-up names only."""
import json
import os

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
    old = f.stat().st_mtime - 60      # saved a minute ago, so a refresh asked for now is clearly newer on any filesystem
    os.utime(f, (old, old))
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
    assert 'href="mailto:pat@example.org"' in html                     # each person's own address, beside their name
    assert 'Also receives: old@example.org' in html and 'retired addresses' in html and 'Refresh from Google' in html


def test_a_volunteer_sees_the_list_and_can_refresh(client, tmp_path, monkeypatch):
    import groups
    monkeypatch.setattr(groups, 'GROUPS_REFRESH', str(tmp_path / 'groups-refresh'))
    client.store.update(perms={'invoices'}, level='volunteer')
    html = client.get('/email-groups/').get_data(as_text=True)
    assert 'autocross@example.org' in html and 'Refresh from Google' in html
    assert 'Pat Director' in html and 'pat@example.org' not in html     # names only for volunteers
    client.post('/email-groups/refresh')
    assert (tmp_path / 'groups-refresh').exists()


def test_refresh_asks_the_server(client, tmp_path, monkeypatch):
    import groups
    monkeypatch.setattr(groups, 'GROUPS_REFRESH', str(tmp_path / 'groups-refresh'))
    assert client.post('/email-groups/refresh').status_code == 302
    assert 'Refreshing' in client.get('/email-groups/').get_data(as_text=True)


def test_pull_only_reads(monkeypatch, tmp_path):
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
    import groups_pull as G
    calls = []

    def fake(*a):
        calls.append(a)
        if a[1] == 'groups':
            return 'email,name,aliases\nre@example.org,DRSCCA Region Executive,\n'
        return 'email,role\nPat@x.com,MANAGER\n'
    monkeypatch.setattr(G, 'gam', fake)
    out = tmp_path / 'g.json'
    G.main(str(out))
    assert all(c[0] == 'print' for c in calls)
    assert json.loads(out.read_text())['groups'][0]['members'] == [{'email': 'pat@x.com', 'role': 'MANAGER'}]
