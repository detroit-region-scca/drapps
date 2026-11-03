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
    c.post('/admin/', data={'title': 'Autocross Director', 'from': 'old@x.com', 'name': 'New', 'email': 'new@x.com'})
    assert not (tmp_path / 'q.json').exists()
    _as(c, monkeypatch, ['Admin'])
    c.post('/admin/', data={'title': 'Autocross Director', 'from': 'old@x.com', 'name': 'New Person', 'email': 'new@x.com'})
    q = json.loads((tmp_path / 'q.json').read_text())['changes']
    assert q[0]['title'] == 'Autocross Director' and q[0]['email'] == 'new@x.com' and q[0]['from'] == 'old@x.com'


def test_needed_role_cannot_lose_its_only_holder(tmp_path, monkeypatch):
    import officials
    monkeypatch.setattr(utils, 'OFFICIALS_QUEUE', str(tmp_path / 'q.json'))
    monkeypatch.setattr(utils, 'OFFICIALS_DONE', str(tmp_path / 'd.json'))
    monkeypatch.setattr(officials, 'all', lambda: [{'role': 'Director', 'title': 'Autocross Director', 'name': 'Old', 'email': 'old@x.com'},
                                                   {'role': 'Equipment', 'title': 'Equipment Committee', 'name': 'Ed', 'email': 'ed@x.com'}])
    c = app.test_client()
    _as(c, monkeypatch, ['Admin'])
    c.post('/admin/', data={'action': 'remove', 'title': 'Autocross Director', 'email': 'old@x.com'})
    assert not (tmp_path / 'q.json').exists()
    c.post('/admin/', data={'action': 'remove', 'title': 'Equipment Committee', 'email': 'ed@x.com'})
    c.post('/admin/', data={'action': 'add', 'title': 'Equipment Committee', 'name': 'New Member', 'email': 'nm@x.com'})
    q = json.loads((tmp_path / 'q.json').read_text())['changes']
    assert [x['op'] for x in q] == ['remove', 'add'] and q[1]['entry']['role'] == 'Equipment'


def test_apply_person_keeps_drops_and_adds_titles():
    import sys
    sys.path.insert(0, 'tools')
    import officials_queue as Q
    rows = [{'role': 'Director', 'title': 'Autocross Director', 'name': 'Mike', 'email': 'm@x.com', 'program': 'AX', 'notify': 'autocross@d.org'},
            {'role': 'Equipment', 'title': 'Equipment Committee', 'name': 'Mike', 'email': 'm@x.com'},
            {'role': 'Equipment', 'title': 'Equipment Committee', 'name': 'Ed', 'email': 'e@x.com'},
            {'role': 'Director at Large', 'title': 'Director at Large', 'name': 'Sue', 'email': 's@x.com'}]
    out = Q.apply_person(rows, {'from': 'm@x.com', 'email': 'mike@x.com', 'name': 'Mike R', 'titles': ['Autocross Director', 'Director at Large']})
    mine = sorted(o['title'] for o in out if o['email'] == 'mike@x.com')
    assert mine == ['Autocross Director', 'Director at Large']
    ax = next(o for o in out if o['title'] == 'Autocross Director')
    assert ax['program'] == 'AX' and ax['name'] == 'Mike R'
    assert any(o['email'] == 'e@x.com' for o in out) and not any(o['email'] == 'm@x.com' for o in out)


def test_users_page_rules(tmp_path, monkeypatch):
    import auth
    import officials
    monkeypatch.setattr(utils, 'OFFICIALS_QUEUE', str(tmp_path / 'q.json'))
    monkeypatch.setattr(utils, 'OFFICIALS_DONE', str(tmp_path / 'd.json'))
    monkeypatch.setattr(officials, 'all', lambda: [
        {'role': 'Director', 'title': 'Autocross Director', 'name': 'Mike', 'email': 'm@x.com', 'program': 'AX'},
        {'role': 'Equipment', 'title': 'Equipment Committee', 'name': 'Mike', 'email': 'm@x.com'},
        {'role': 'Admin', 'title': 'Administrator', 'name': 'Admin', 'email': 'admin@drscca.org'}])
    vols = {}
    monkeypatch.setattr(auth, 'volunteers', lambda: vols)
    calls = []
    monkeypatch.setattr(auth, 'set_volunteer', lambda e, n=None, note='', by='': calls.append((e, n)) or 'added')
    c = app.test_client()
    _as(c, monkeypatch, ['Admin'])
    q = lambda: json.loads((tmp_path / 'q.json').read_text())['changes'] if (tmp_path / 'q.json').exists() else []  # noqa: E731
    # the only Autocross Director cannot drop it
    c.post('/admin/', data={'action': 'user', 'old_email': 'm@x.com', 'name': 'Mike', 'email': 'm@x.com', 'title': ['Equipment Committee']})
    assert q() == []
    # dropping Equipment, keeping Autocross: queued as one person change
    c.post('/admin/', data={'action': 'user', 'old_email': 'm@x.com', 'name': 'Mike', 'email': 'm@x.com', 'title': ['Autocross Director']})
    assert q()[-1]['op'] == 'person' and q()[-1]['titles'] == ['Autocross Director']
    # a new volunteer goes to the volunteers list, not the officials list
    n = len(q())
    c.post('/admin/', data={'action': 'user', 'old_email': '', 'name': 'Val', 'email': 'val@x.com', 'volunteer': 'yes'})
    assert calls == [('val@x.com', 'Val')] and len(q()) == n
    # region accounts are not editable here
    c.post('/admin/', data={'action': 'user', 'old_email': 'admin@drscca.org', 'name': 'X', 'email': 'admin@drscca.org'})
    assert len(q()) == n


def test_bracketed_approver_follows_its_job():
    import sys
    sys.path.insert(0, 'tools')
    import officials_queue as Q
    rows = [{'role': 'RE', 'title': 'Region Executive', 'name': 'Alex', 'email': 'a@x.com'},
            {'role': 'Director', 'title': 'General Events approver (Region Executive)', 'name': 'Alex', 'email': 'a@x.com', 'program': 'DR'},
            {'role': 'Director at Large', 'title': 'Director at Large', 'name': 'Bo', 'email': 'b@x.com'}]
    out = Q.apply_person(rows, {'from': 'b@x.com', 'email': 'b@x.com', 'name': 'Bo', 'titles': ['Director at Large', 'Region Executive']})
    out = Q.apply_person(out, {'from': 'a@x.com', 'email': 'a@x.com', 'name': 'Alex', 'titles': []})
    ge = next(o for o in out if o['title'].startswith('General Events'))
    assert (ge['email'], ge['program']) == ('b@x.com', 'DR')
    assert not any(o['email'] == 'a@x.com' for o in out)
