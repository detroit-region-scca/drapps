"""Election handover: edits officials.json only; it never calls Google (the groups are changed only in Google)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import handover as H  # noqa: E402

OFFICIALS = [
    {'role': 'RE', 'title': 'Region Executive', 'name': 'Alex', 'email': 'alex@x.com'},
    {'role': 'Director', 'title': 'General Events approver (Region Executive)', 'name': 'Alex', 'email': 'alex@x.com'},
    {'role': 'Equipment', 'title': 'Equipment Committee', 'name': 'Alex', 'email': 'alex@x.com'},
    {'role': 'Treasurer', 'title': 'Treasurer', 'name': 'T', 'email': 'treasurer@drscca.org'},
    {'role': 'Director', 'title': 'Autocross Director', 'name': 'Mike', 'email': 'mike@x.com'},
    {'role': 'Director at Large', 'title': 'Director at Large', 'name': 'A', 'email': 'a@x.com'},
    {'role': 'Director at Large', 'title': 'Director at Large', 'name': 'B', 'email': 'b@x.com'},
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    f = tmp_path / 'officials.json'
    f.write_text(json.dumps({'officials': OFFICIALS}))
    monkeypatch.setattr(H, 'OFFICIALS', str(f))
    return f, None, None


def test_handover_has_no_google_groups_code():
    assert not hasattr(H, 'gam') and 'subprocess' not in open(H.__file__).read()


def test_handover_moves_the_job_and_its_bracketed_duties_only(env):
    f, _, _ = env
    H.main(['hand', 'Region Executive', 'new@x.com', 'New', '--apply'])
    rows = json.loads(f.read_text())['officials']
    assert [o['email'] for o in rows[:3]] == ['new@x.com', 'new@x.com', 'alex@x.com']   # Alex keeps Equipment


def test_dry_run_changes_nothing(env):
    f, _, _ = env
    before = f.read_text()
    H.main(['hand', 'Autocross Director', 'new@x.com', 'New'])
    assert f.read_text() == before


def test_shared_title_needs_from(env):
    with pytest.raises(SystemExit):
        H.main(['hand', 'Director at Large', 'n@x.com', 'N'])
    H.main(['hand', 'Director at Large', 'n@x.com', 'N', '--from', 'b@x.com', '--apply'])
    assert {o['email'] for o in json.loads(env[0].read_text())['officials'] if o['role'] == 'Director at Large'} == {'a@x.com', 'n@x.com'}


def test_region_accounts_hand_over_by_password(env):
    with pytest.raises(SystemExit):
        H.main(['hand', 'Treasurer', 'n@x.com', 'N'])
