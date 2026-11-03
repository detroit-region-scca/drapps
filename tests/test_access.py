import access

EVERY = access.EVERYDAY_DEFAULT


def test_defaults_and_per_role_permissions():
    assert access.perms('Treasurer') == set(access.PERMS) - {'plan_own'}
    assert access.perms('Director') == EVERY
    assert access.perms('Equipment') == EVERY | {'ops'} and access.perms('Points Keeper') == EVERY | {'points'}
    assert access.perms('Volunteer') == {'funds', 'invoices', 'points_enter'}
    access.save('Director', {'funds', 'plan_own', 'events'})
    assert access.perms('Director') == {'funds', 'plan_own', 'events'}
    access.save('Treasurer', {'pay', 'audit', 'plan_all', 'plan_own'})
    assert access.perms('Treasurer') == {'pay', 'audit', 'plan_all'}         # any program includes their own
    assert access.perms_for(['Director', 'Equipment']) >= {'plan_own', 'ops'}
    access.save('Admin', set())                       # the admin account cannot lose anything
    assert access.perms('Admin') >= set(access.EXTRA)


def test_earlier_full_standard_format_still_reads(monkeypatch, tmp_path):
    import json
    p = tmp_path / 'old.json'
    p.write_text(json.dumps({'Director': {'level': 'full', 'ops': False}, 'Equipment': {'level': 'standard', 'ops': True}}))
    monkeypatch.setattr(access, 'PATH', str(p))
    access._cache.clear()
    assert access.perms('Director') >= set(access.EXTRA) and access.perms('Equipment') == EVERY | {'ops'}
