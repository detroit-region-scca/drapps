"""App email goes to the role's group ("notify"); sign-in still matches the person's own "email"."""
import json

import officials as O


def _use(tmp_path, monkeypatch, rows):
    f = tmp_path / 'officials.json'
    f.write_text(json.dumps({'officials': rows}))
    monkeypatch.setattr(O, 'PATH', str(f))
    O._cache.clear()


def test_director_and_signers_use_the_group(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, [
        {'role': 'Director', 'program': 'AX', 'title': 'Autocross Director', 'name': 'Mike', 'email': 'mike@x.com', 'notify': 'autocross@drscca.org'},
        {'role': 'RE', 'title': 'Region Executive', 'name': 'Alex', 'email': 'alex@x.com', 'notify': 're@drscca.org'},
        {'role': 'ARE', 'title': 'Assistant Region Executive', 'name': 'Matt', 'email': 'matt@x.com'}])
    assert O.director('AX') == ('Mike', 'autocross@drscca.org')
    assert O.signers() == [('Alex', 're@drscca.org')]   # Matt's role has no group: nobody is emailed
    assert [o['email'] for o in O.all()] == ['mike@x.com', 'alex@x.com', 'matt@x.com']   # sign-in addresses unchanged


def test_missing_director(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, [])
    assert O.director('AX') == ('', '')


def test_no_group_means_no_email(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, [{'role': 'ARE', 'title': 'ARE', 'name': 'Matt', 'email': 'matt@x.com'}])
    assert O.signers() == []          # never falls back to the person's own inbox


def test_signin_codes_go_to_the_group(tmp_path, monkeypatch):
    import auth
    _use(tmp_path, monkeypatch, [
        {'role': 'RE', 'title': 'Region Executive', 'name': 'Alex', 'email': 'alex@x.com', 'notify': 're@drscca.org'},
        {'role': 'Equipment', 'title': 'Equipment Committee', 'name': 'Alex', 'email': 'alex@x.com', 'notify': 'equipment@drscca.org'},
        {'role': 'Treasurer', 'title': 'Treasurer', 'name': 'Nathan', 'email': 'admin@drscca.org', 'notify': 'treasurer@drscca.org'},
        {'role': 'Financial Director', 'title': 'Financial Director', 'name': 'Sonja', 'email': 'membership@drscca.org', 'notify': 'treasurer@drscca.org'},
        {'role': 'Director at Large', 'title': 'Director at Large', 'name': 'Eric', 'email': 'eric@x.com'}])
    monkeypatch.setattr(auth, 'volunteers', lambda: {'vol@x.com': {'name': 'Vee'}})
    assert auth.code_address('alex@x.com') == 're@drscca.org'          # first role with a group, not equipment@
    assert auth.code_address('re@drscca.org') == 're@drscca.org'
    assert auth.code_address('vol@x.com') == 'vol@x.com'                # volunteers are provisioned one by one
    assert auth.code_address('eric@x.com') == 'eric@x.com'              # no group for the role: own address
    u = auth.lookup('treasurer@drscca.org')
    assert u['name'] == 'Nathan / Sonja' and u['level'] == 'officer'
    assert auth.lookup('alex@x.com')['name'] == 'Alex'
