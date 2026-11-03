import equip_store as E
from werkzeug.datastructures import MultiDict


def test_norm():
    assert E.norm('1,200') == E.norm(1200) == E.norm('1200.0') == E.norm('$1200') == '1200'
    assert E.norm(None) == E.norm('') == ''
    assert E.norm(' Wixom ') == 'Wixom'


def test_only_fields_the_person_changed():
    import equip
    base = {'name': 'Pylons', 'cost': 3900.0, 'year': 2025.0, 'notes': 'old'}
    import json
    f = MultiDict({'_base': json.dumps(base), 'name': 'Pylons', 'cost': '3,900', 'year': '2026', 'notes': 'old'})
    assert equip._changed(f, ['id', 'name', 'cost', 'year', 'notes'], base) == {'year'}
    f = MultiDict({'name': 'Pylons', 'cost': '3900'})          # an old form without _base: everything it sent
    assert equip._changed(f, ['id', 'name', 'cost', 'year'], base) == {'name', 'cost'}
