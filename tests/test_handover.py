"""Election handover: officials.json decides group membership; a handover edits the file and only plain members move."""
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
    groups = {g: {e: 'MEMBER' for e in es} for g, es in H.wanted(OFFICIALS).items()}
    groups['officers']['treasurer@drscca.org'] = 'OWNER'
    calls = []

    def fake_gam(*args):
        calls.append(args)
        if args[:2] == ('print', 'group-members'):
            g = args[3].split('@')[0]
            return 'email,role\n' + ''.join(f'{e},{r}\n' for e, r in groups.get(g, {}).items())
        return ''
    monkeypatch.setattr(H, 'gam', fake_gam)
    return f, groups, calls


def test_groups_follow_roles():
    w = H.wanted(OFFICIALS)
    assert w['re'] == {'alex@x.com'} and w['autocross'] == {'mike@x.com'}
    assert w['officers'] == {'alex@x.com', 'treasurer@drscca.org'}
    assert w['equipment'] == {'alex@x.com'} and 'a@x.com' in w['bod']


def test_in_sync_means_no_steps(env):
    assert H.plan(H.wanted(OFFICIALS)) == []


def test_owner_is_never_removed(env):
    _, groups, _ = env
    groups['officers']['boss@x.com'] = 'OWNER'
    assert H.plan(H.wanted(OFFICIALS)) == []


def test_handover_moves_the_job_and_its_bracketed_duties_only(env):
    f, _, calls = env
    H.main(['hand', 'Region Executive', 'new@x.com', 'New', '--apply'])
    rows = json.loads(f.read_text())['officials']
    assert [o['email'] for o in rows[:3]] == ['new@x.com', 'new@x.com', 'alex@x.com']   # Alex keeps Equipment
    ops = {(c[2].split('@')[0], c[3], c[5]) for c in calls if c[:2] == ('update', 'group')}
    assert ('re', 'add', 'new@x.com') in ops and ('re', 'remove', 'alex@x.com') in ops
    assert ('officers', 'remove', 'alex@x.com') in ops and not any(g == 'equipment' for g, _, _ in ops)


def test_dry_run_changes_nothing(env):
    f, _, calls = env
    before = f.read_text()
    H.main(['hand', 'Autocross Director', 'new@x.com', 'New'])
    assert f.read_text() == before and not any(c[0] == 'update' for c in calls)


def test_shared_title_needs_from(env):
    with pytest.raises(SystemExit):
        H.main(['hand', 'Director at Large', 'n@x.com', 'N'])
    H.main(['hand', 'Director at Large', 'n@x.com', 'N', '--from', 'b@x.com', '--apply'])
    assert {o['email'] for o in json.loads(env[0].read_text())['officials'] if o['role'] == 'Director at Large'} == {'a@x.com', 'n@x.com'}


def test_region_accounts_hand_over_by_password(env):
    with pytest.raises(SystemExit):
        H.main(['hand', 'Treasurer', 'n@x.com', 'N'])
