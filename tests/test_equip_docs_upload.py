import equip_docs as D


def test_file_name_starts_with_the_document_so_it_counts():
    n = D.file_name('Title', '2003 Ford Gulf Stream motorhome', '2026', 'IMG_1234.JPG')
    assert n == 'Title - 2003 Ford Gulf Stream motorhome - 2026.jpg'
    assert D.matches('Title', n)
    assert D.file_name('Insurance card', "22' race trailer", '', 'scan.pdf', 2) == "Insurance card - 22' race trailer (2).pdf"


def test_file_name_drops_characters_drive_and_phones_dislike():
    assert D.file_name('Registration', 'A/B: trailer', '', 'x') == 'Registration - A B trailer'


def test_kinds_put_missing_first_then_needed_then_common():
    row = {'needed': 'Title, Registration, Insurance card', 'missing': ['Registration']}
    k = D.kinds_for(row)
    assert k[:3] == ['Registration', 'Title', 'Insurance card']
    assert 'Bill of sale' in k and k.count('Title') == 1
