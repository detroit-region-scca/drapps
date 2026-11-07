"""Records: public, read-only pages over data/records.db (made-up names only: this repo is public)."""
import sqlite3

import records


def _db(path):
    c = sqlite3.connect(path)
    c.executescript('''
    CREATE TABLE events (id INTEGER PRIMARY KEY, program TEXT, year INTEGER, date TEXT, name TEXT);
    CREATE TABLE files (id INTEGER PRIMARY KEY, event_id INTEGER, program TEXT, year INTEGER, date TEXT, kind TEXT, name TEXT, drive_url TEXT);
    CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE result_rows (id INTEGER PRIMARY KEY, file_id INTEGER, event_id INTEGER, person_id INTEGER, program TEXT, year INTEGER,
      date TEXT, section TEXT, position TEXT, class TEXT, car_number TEXT, driver TEXT, vehicle TEXT, total TEXT, raw_time TEXT,
      pax_time TEXT, total_s REAL, raw_s REAL, pax_s REAL, line_no INTEGER);
    CREATE TABLE season_standings (id INTEGER PRIMARY KEY, program TEXT, year INTEGER, class TEXT, place INTEGER, person_id INTEGER,
      name TEXT, score REAL, file_id INTEGER);
    INSERT INTO events VALUES (1, 'Autocross', 2025, '2025-05-25', 'Test Opener');
    INSERT INTO files VALUES (1, 1, 'Autocross', 2025, '2025-05-25', 'event', '2025-05-25 results.pdf', 'https://drive.example/secret');
    INSERT INTO files VALUES (2, NULL, 'Autocross', 2025, NULL, 'points', 'points.pdf', 'https://drive.example/points');
    INSERT INTO people VALUES (1, 'Alex Example'), (2, 'Season Opener Points');
    INSERT INTO result_rows VALUES (1, 1, 1, 1, 'Autocross', 2025, '2025-05-25', 'ss', '1', 'ss', '7', 'Alex Example', '2020 Test Car',
      '45.123', NULL, NULL, 45.123, NULL, NULL, 10);
    INSERT INTO season_standings VALUES (1, 'Autocross', 2025, 'SS - Super Street', 1, 1, 'Alex Example', 500, 2);
    INSERT INTO season_standings VALUES (2, 'Autocross', 2025, 'SS - Super Street', 2, 2, 'Season Opener Points', 400, 2);
    ''')
    c.commit(); c.close()


def test_records_pages_are_public_and_hide_drive_links(tmp_path, monkeypatch):
    from app import app
    path = str(tmp_path / 'records.db'); _db(path)
    monkeypatch.setattr(records, 'PATH', path)
    app.config['TESTING'] = True
    c = app.test_client()
    for url in ('/records/', '/records/?q=alex', '/records/hall-of-fame', '/records/autocross', '/records/autocross/2025',
                '/records/event/1', '/records/person/1'):
        r = c.get(url)
        assert r.status_code == 200, url
        assert b'drive.example' not in r.data          # the region Drive is private: no file links for visitors
    assert b'Alex Example' in c.get('/records/hall-of-fame').data
    assert b'Season Opener Points' not in c.get('/records/hall-of-fame').data   # page text read as a name stays out
    assert c.get('/records/autocross/1990').status_code == 404
    assert c.get('/records/nope').status_code == 404


def test_records_show_drive_links_when_signed_in(tmp_path, monkeypatch):
    import auth
    from app import app
    path = str(tmp_path / 'records.db'); _db(path)
    monkeypatch.setattr(records, 'PATH', path)
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Pat', 'level': 'official', 'roles': [], 'perms': set()} if e else None)
    app.config['TESTING'] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='pat@example.org', who='Pat')
    assert b'drive.example/secret' in c.get('/records/event/1').data


def test_records_off_without_a_database(tmp_path, monkeypatch):
    from app import app
    monkeypatch.setattr(records, 'PATH', str(tmp_path / 'none.db'))
    app.config['TESTING'] = True
    assert app.test_client().get('/records/').status_code == 404
