import access


def test_defaults_and_per_role_permissions():
    assert access.perms('Treasurer') == set(access.PERMS)
    assert access.perms('Director') == set()
    assert access.perms('Equipment') == {'ops'} and access.perms('Points Keeper') == {'points'}
    access.save('Director at Large', {'board', 'audit'})
    assert access.perms('Director at Large') == {'board', 'audit'}
    access.save('Treasurer', {'pay', 'audit'})
    assert access.perms('Treasurer') == {'pay', 'audit'}
    assert access.perms_for(['Treasurer', 'Equipment']) == {'pay', 'audit', 'ops'}
    access.save('Admin', set())                       # the admin account cannot lose anything
    assert access.perms('Admin') == set(access.PERMS)


def test_earlier_full_standard_format_still_reads(monkeypatch, tmp_path):
    import json
    p = tmp_path / 'old.json'
    p.write_text(json.dumps({'Director': {'level': 'full', 'ops': False}, 'Equipment': {'level': 'standard', 'ops': True}}))
    monkeypatch.setattr(access, 'PATH', str(p))
    access._cache.clear()
    assert access.perms('Director') == set(access.PERMS) and access.perms('Equipment') == {'ops'}
