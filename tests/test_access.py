import access

EVERY = access.EVERYDAY_DEFAULT
APPS = set(access.APPS)


def core(role):
    return access.perms(role) - APPS


def test_defaults_and_per_role_permissions():
    assert core('Treasurer') == (set(access.EVERYDAY) | set(access.EXTRA)) - {'plan_own'}
    assert core('Director') == EVERY
    assert core('Equipment') == EVERY | {'ops'} and core('Points Keeper') == EVERY | {'points'}
    assert core('Volunteer') == {'funds', 'invoices', 'points_enter'}
    access.save('Director', {'funds', 'plan_own', 'events'})
    assert core('Director') == {'funds', 'plan_own', 'events'}
    access.save('Treasurer', {'pay', 'audit', 'plan_all', 'plan_own'})
    assert core('Treasurer') == {'pay', 'audit', 'plan_all'}         # any program includes their own
    assert access.perms_for(['Director', 'Equipment']) >= {'plan_own', 'ops'}
    access.save('Admin', set())                       # the admin account cannot lose anything
    assert access.perms('Admin') >= set(access.EXTRA)


def test_home_apps_always_follow_permissions():
    assert {'show_operations', 'show_treasurer'} <= access.perms('Treasurer')
    assert 'show_operations' in access.perms('Equipment') and 'show_operations' not in access.perms('Director')
    assert access.perms('Volunteer') & APPS == {'show_members', 'show_funds', 'show_invoices'}
    access.save('Director', set(access.EVERYDAY_DEFAULT) | {'show_operations'})     # a tile can't be set by hand
    assert 'show_operations' not in access.perms('Director')
    access.save('Director', set(access.EVERYDAY_DEFAULT) | {'ops'})                 # it follows the permission
    assert 'show_operations' in access.perms('Director')


def test_earlier_full_standard_format_still_reads(monkeypatch, tmp_path):
    import json
    p = tmp_path / 'old.json'
    p.write_text(json.dumps({'Director': {'level': 'full', 'ops': False}, 'Equipment': {'level': 'standard', 'ops': True}}))
    monkeypatch.setattr(access, 'PATH', str(p))
    access._cache.clear()
    assert core('Director') >= set(access.EXTRA) - {'admin'} and core('Equipment') == EVERY | {'ops'}


def test_half_written_officials_file_keeps_the_last_good_list(tmp_path, monkeypatch):
    import json
    import os
    import officials
    f = tmp_path / 'o.json'
    f.write_text(json.dumps({'officials': [{'role': 'Treasurer', 'name': 'Pat', 'email': 'p@x.com'}]}))
    monkeypatch.setattr(officials, 'PATH', str(f))
    officials._cache.clear()
    assert [o['name'] for o in officials.all()] == ['Pat']
    f.write_text('{"officials": [ {"role": "Tre')          # caught mid-write
    os.utime(f, ns=(1, 1))
    assert [o['name'] for o in officials.all()] == ['Pat']


def test_add_and_remove_role(tmp_path, monkeypatch):
    import access
    monkeypatch.setattr(access, 'PATH', str(tmp_path / 'role-access.json'))
    access._cache.clear()
    access.add_role('Trophy Committee')
    assert 'Trophy Committee' in access.saved_roles()
    assert 'funds' in access.perms('Trophy Committee') and 'pay' not in access.perms('Trophy Committee')
    access.add_role('Helper', like='Treasurer')
    assert 'pay' in access.perms('Helper')
    access.remove_role('Trophy Committee')
    assert 'Trophy Committee' not in access.saved_roles()


def test_first_holder_of_new_role():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location('oq', pathlib.Path(__file__).parent.parent / 'tools' / 'officials_queue.py')
    oq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oq)
    rows = [{'role': 'Treasurer', 'title': 'Treasurer', 'name': 'A', 'email': 'a@x.test'}]
    out = oq.apply_person(rows, {'from': '', 'name': 'B', 'email': 'b@x.test', 'titles': ['Trophy Committee'],
                                 'role_of': {'Trophy Committee': 'Trophy Committee'}})
    assert {'role': 'Trophy Committee', 'title': 'Trophy Committee', 'name': 'B', 'email': 'b@x.test'} in out
