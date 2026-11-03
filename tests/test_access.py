import access


def test_defaults_and_per_role_settings():
    assert access.level('Treasurer') == 'full' and access.level('Director') == 'standard'
    assert access.edits_ops('Equipment') and not access.edits_ops('Director') and access.edits_ops('RE')
    access.save('Director at Large', 'full', False)
    access.save('Equipment', 'standard', False)
    assert access.level('Director at Large') == 'full' and access.edits_ops('Director at Large')
    assert not access.edits_ops('Equipment')
    access.save('Admin', 'standard', False)          # the admin account cannot be downgraded
    assert access.level('Admin') == 'full'
