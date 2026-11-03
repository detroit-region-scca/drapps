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


def test_home_apps_follow_permissions_until_set():
    assert {'show_operations', 'show_treasurer'} <= access.perms('Treasurer')
    assert 'show_operations' in access.perms('Equipment') and 'show_operations' not in access.perms('Director')
    assert access.perms('Volunteer') & APPS == {'show_members', 'show_funds', 'show_invoices'}
    access.save('Director', set(access.EVERYDAY_DEFAULT) | {'show_operations'})
    assert 'show_operations' in access.perms('Director') and 'show_board' not in access.perms('Director')


def test_earlier_full_standard_format_still_reads(monkeypatch, tmp_path):
    import json
    p = tmp_path / 'old.json'
    p.write_text(json.dumps({'Director': {'level': 'full', 'ops': False}, 'Equipment': {'level': 'standard', 'ops': True}}))
    monkeypatch.setattr(access, 'PATH', str(p))
    access._cache.clear()
    assert core('Director') >= set(access.EXTRA) - {'admin'} and core('Equipment') == EVERY | {'ops'}
