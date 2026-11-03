import json

import utils
from app import app


def _as(c, monkeypatch, roles):
    import auth
    with c.session_transaction() as s:
        s['email'] = 'x@example.org'
    import access
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Someone', 'level': 'officer', 'roles': roles,
                                                   'perms': sorted(access.perms_for(roles))} if e else None)


def test_only_admin_can_queue_a_change(tmp_path, monkeypatch):
    import auth
    import officials
    monkeypatch.setattr(utils, 'OFFICIALS_QUEUE', str(tmp_path / 'q.json'))
    monkeypatch.setattr(utils, 'OFFICIALS_DONE', str(tmp_path / 'd.json'))
    monkeypatch.setattr(officials, 'all', lambda: [{'role': 'Equipment', 'title': 'Equipment Committee', 'name': 'Old', 'email': 'old@x.com'}])
    monkeypatch.setattr(auth, 'volunteers', lambda: {})
    form = {'action': 'user', 'old_email': 'old@x.com', 'name': 'Old Name', 'email': 'old@x.com', 'title': ['Equipment Committee']}
    c = app.test_client()
    _as(c, monkeypatch, ['Director'])
    assert c.post('/admin/', data=form).status_code == 404
    assert not (tmp_path / 'q.json').exists()
    _as(c, monkeypatch, ['Admin'])
    c.post('/admin/', data=form)
    q = json.loads((tmp_path / 'q.json').read_text())['changes']
    assert q[0]['op'] == 'person' and q[0]['name'] == 'Old Name'


def test_old_actions_cannot_make_someone_admin(tmp_path, monkeypatch):
    import officials
    monkeypatch.setattr(utils, 'OFFICIALS_QUEUE', str(tmp_path / 'q.json'))
    monkeypatch.setattr(officials, 'all', lambda: [{'role': 'Admin', 'title': 'Administrator', 'name': 'Admin', 'email': 'admin@drscca.org'}])
    c = app.test_client()
    _as(c, monkeypatch, ['Treasurer'])
    c.post('/admin/', data={'action': 'add', 'title': 'Administrator', 'name': 'Me', 'email': 'me@x.com'})
    assert not (tmp_path / 'q.json').exists()


def test_applier_refuses_to_leave_a_needed_role_empty():
    import sys
    import pytest
    sys.path.insert(0, 'tools')
    import officials_queue as Q
    before = [{'role': 'Treasurer', 'title': 'Treasurer', 'name': 'A', 'email': 'a@x.com'},
              {'role': 'Treasurer', 'title': 'Treasurer', 'name': 'B', 'email': 'b@x.com'}]
    with pytest.raises(SystemExit):
        Q.check_needed(before, [])
    Q.check_needed(before, before[:1])                    # one holder left is fine


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


def test_roles_grid_saves_only_changed_roles(monkeypatch):
    import access
    import auth
    import officials
    monkeypatch.setattr(officials, 'all', lambda: [{'role': 'Director', 'title': 'Autocross Director', 'name': 'M', 'email': 'm@x.com'},
                                                   {'role': 'Equipment', 'title': 'Equipment Committee', 'name': 'E', 'email': 'e@x.com'}])
    monkeypatch.setattr(auth, 'volunteers', lambda: {})
    c = app.test_client()
    _as(c, monkeypatch, ['Admin'])
    form = {f'{r}::{p}': 'yes' for r in ('Director', 'Equipment', 'Volunteer') for p in access.perms(r)}
    form['Director::audit'] = 'yes'                     # one new box
    c.post('/admin/roles', data=form)
    assert 'audit' in access.perms('Director')
    assert set(access._load()) == {'Director'}           # Equipment and Volunteer untouched, still on defaults


def test_hand_over_queues_one_change(tmp_path, monkeypatch):
    import auth
    import officials
    monkeypatch.setattr(utils, 'OFFICIALS_QUEUE', str(tmp_path / 'q.json'))
    monkeypatch.setattr(utils, 'OFFICIALS_DONE', str(tmp_path / 'd.json'))
    monkeypatch.setattr(officials, 'all', lambda: [{'role': 'Treasurer', 'title': 'Treasurer', 'name': 'A', 'email': 'a@x.com'},
                                                   {'role': 'Director at Large', 'title': 'Director at Large', 'name': 'B', 'email': 'b@x.com'}])
    monkeypatch.setattr(auth, 'volunteers', lambda: {})
    c = app.test_client()
    _as(c, monkeypatch, ['Admin'])
    c.post('/admin/', data={'action': 'handover', 'old_email': 'a@x.com', 'title': 'Treasurer', 'to': 'b@x.com'})
    q = json.loads((tmp_path / 'q.json').read_text())['changes']
    assert len(q) == 1 and (q[0]['op'], q[0]['title'], q[0]['from'], q[0]['email'], q[0]['name']) == ('change', 'Treasurer', 'a@x.com', 'b@x.com', 'B')
    c.post('/admin/', data={'action': 'handover', 'old_email': 'a@x.com', 'title': 'Treasurer', 'to': 'b@x.com'})
    assert len(json.loads((tmp_path / 'q.json').read_text())['changes']) == 1     # still pending: refused
