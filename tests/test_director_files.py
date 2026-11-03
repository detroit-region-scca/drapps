import officials as O


def test_director_email(monkeypatch):
    monkeypatch.setattr(O, 'all', lambda: [{'role': 'Director', 'program': 'AX', 'name': 'Mike', 'email': 'Mike@X.com'},
                                           {'role': 'Equipment', 'program': '', 'name': 'Mike', 'email': 'mike@x.com'}])
    assert O.director_email('AX') == 'mike@x.com'
    assert O.director_email('RX') == ''
