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
    assert O.signers() == [('Alex', 're@drscca.org'), ('Matt', 'matt@x.com')]   # no group: falls back to the person
    assert [o['email'] for o in O.all()] == ['mike@x.com', 'alex@x.com', 'matt@x.com']   # sign-in addresses unchanged


def test_missing_director(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, [])
    assert O.director('AX') == ('', '')
