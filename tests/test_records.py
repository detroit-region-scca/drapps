"""Records: read-only pages over data/records.db, for anyone signed in (made-up names only: this repo is public)."""
import sqlite3

import records


def _db(path):
    c = sqlite3.connect(path)
    c.executescript('''
    CREATE TABLE events (id INTEGER PRIMARY KEY, program TEXT, year INTEGER, date TEXT, name TEXT);
    CREATE TABLE files (id INTEGER PRIMARY KEY, event_id INTEGER, program TEXT, year INTEGER, date TEXT, kind TEXT, name TEXT, drive_url TEXT, label TEXT, sort INTEGER, ocr TEXT);
    CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT);
    CREATE TABLE result_rows (id INTEGER PRIMARY KEY, file_id INTEGER, event_id INTEGER, person_id INTEGER, program TEXT, year INTEGER,
      date TEXT, section TEXT, position TEXT, class TEXT, car_number TEXT, driver TEXT, vehicle TEXT, total TEXT, raw_time TEXT,
      pax_time TEXT, total_s REAL, raw_s REAL, pax_s REAL, line_no INTEGER);
    CREATE TABLE season_standings (id INTEGER PRIMARY KEY, program TEXT, year INTEGER, class TEXT, place INTEGER, person_id INTEGER,
      name TEXT, score REAL, file_id INTEGER);
    INSERT INTO events VALUES (1, 'Autocross', 2025, '2025-05-25', 'Test Opener');
    INSERT INTO files VALUES (1, 1, 'Autocross', 2025, '2025-05-25', 'event', '2025-05-25 results.pdf', 'https://drive.example/secret', 'Class results', 1, NULL);
    INSERT INTO files VALUES (2, NULL, 'Autocross', 2025, NULL, 'points', 'points.pdf', 'https://drive.example/points', 'Results', 4, NULL);
    INSERT INTO people VALUES (1, 'Alex Example'), (2, 'Season Opener Points');
    INSERT INTO result_rows VALUES (1, 1, 1, 1, 'Autocross', 2025, '2025-05-25', 'ss', '1', 'ss', '7', 'Alex Example', '2020 Test Car',
      '45.123', NULL, NULL, 45.123, NULL, NULL, 10);
    INSERT INTO season_standings VALUES (1, 'Autocross', 2025, 'SS - Super Street', 1, 1, 'Alex Example', 500, 2);
    INSERT INTO season_standings VALUES (2, 'Autocross', 2025, 'SS - Super Street', 2, 2, 'Season Opener Points', 400, 2);
    ''')
    c.commit(); c.close()


def test_records_need_sign_in(tmp_path, monkeypatch):
    from app import app
    path = str(tmp_path / 'records.db'); _db(path)
    monkeypatch.setattr(records, 'PATH', path)
    app.config['TESTING'] = True
    r = app.test_client().get('/records/hall-of-fame')
    assert r.status_code == 302 and b'Alex Example' not in r.data


def test_records_pages_for_a_volunteer(tmp_path, monkeypatch):
    import auth
    from app import app
    path = str(tmp_path / 'records.db'); _db(path)
    monkeypatch.setattr(records, 'PATH', path)
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Vol', 'level': 'volunteer', 'roles': [], 'perms': set()} if e else None)
    app.config['TESTING'] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='vol@example.org', who='Vol')
    for url in ('/records/', '/records/?q=alex', '/records/hall-of-fame', '/records/autocross', '/records/autocross/2025',
                '/records/event/1', '/records/person/1'):
        assert c.get(url).status_code == 200, url
    assert b'Alex Example' in c.get('/records/hall-of-fame').data
    assert b'Season Opener Points' not in c.get('/records/hall-of-fame').data   # page text read as a name stays out
    assert c.get('/records/autocross/1990').status_code == 404


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
    import auth
    from app import app
    monkeypatch.setattr(records, 'PATH', str(tmp_path / 'none.db'))
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Pat', 'level': 'official', 'roles': [], 'perms': set()} if e else None)
    app.config['TESTING'] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='pat@example.org', who='Pat')
    assert c.get('/records/').status_code == 404


def test_add_results_saves_files_to_the_inbox(tmp_path, monkeypatch):
    import io
    import auth
    import storage
    from app import app
    path = str(tmp_path / 'records.db'); _db(path)
    monkeypatch.setattr(records, 'PATH', path)
    monkeypatch.setattr(records, 'INBOX', 'inbox-folder')
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Vol', 'level': 'volunteer', 'roles': [], 'perms': set()} if e else None)
    sent = []

    class Resp:
        def __init__(self, data=None): self.data = data or {}
        def raise_for_status(self): pass
        def json(self): return self.data

    class Sess:
        def post(self, url, params=None, files=None, **k):
            import json
            sent.append(json.loads(files['metadata'][1])); return Resp({'id': 'x'})
        def get(self, url, params=None, **k): return Resp({'files': []})

    class Client:
        class http_client:
            session = Sess()
    monkeypatch.setattr(storage, 'sheets_client', lambda: Client)
    monkeypatch.setattr('utils.log_audit_event', lambda *a, **k: None)
    app.config['TESTING'] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='vol@example.org', who='Vol', _csrf='t')
    assert c.get('/records/add').status_code == 200
    r = c.post('/records/add', data={'_csrf': 't', 'program': 'Autocross', 'date': '2026-11-14', 'event': 'Turkey/Trot',
                                     'files': (io.BytesIO(b'%PDF-1.4'), 'ClassResults.pdf')}, content_type='multipart/form-data')
    assert r.status_code == 302
    assert sent[0]['name'] == '2026-11-14 - Autocross - Turkey Trot - ClassResults.pdf' and sent[0]['parents'] == ['inbox-folder']
    assert 'by Vol' in sent[0]['description']
    sent.clear()
    c.post('/records/add', data={'_csrf': 't', 'program': 'Autocross', 'event': 'X', 'files': (io.BytesIO(b'MZ'), 'tool.exe')},
           content_type='multipart/form-data')
    assert sent == []


def test_person_shows_one_line_per_event_with_pax_place(tmp_path, monkeypatch):
    import sqlite3 as S
    import auth
    from app import app
    path = str(tmp_path / 'records.db'); _db(path)
    c = S.connect(path)
    c.executescript('''
    INSERT INTO files VALUES (3, 1, 'Autocross', 2025, '2025-05-25', 'event', 'pax.pdf', 'https://drive.example/pax', 'PAX', 2, NULL);
    INSERT INTO result_rows VALUES (2, 1, 1, 1, 'Autocross', 2025, '2025-05-25', 'pro', '3', 'ss', '7', 'Alex Example', '2020 Test Car',
      '45.123', NULL, NULL, 45.123, NULL, NULL, 5);
    INSERT INTO result_rows VALUES (3, 3, 1, 1, 'Autocross', 2025, '2025-05-25', '', '12', 'ss', '7', 'Alex Example', '2020 Test Car',
      '45.123', NULL, '38.100', 45.123, NULL, 38.1, 3);
    ''')
    c.commit(); c.close()
    monkeypatch.setattr(records, 'PATH', path)
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Pat', 'level': 'official', 'roles': [], 'perms': set()} if e else None)
    app.config['TESTING'] = True
    cl = app.test_client()
    with cl.session_transaction() as s:
        s.update(authenticated=True, email='pat@example.org', who='Pat')
    html = cl.get('/records/person/1').data.decode()
    assert html.count('/records/event/1"') == 1 and 'Events (1)' in html
    assert '<td class="num">1</td><td class="num">12</td>' in html      # own class place 1 (not the Pro list's 3), PAX place 12
    ev = cl.get('/records/event/1').data.decode()
    assert 'PAX' in ev and 'Class results' in ev


def test_unlinked_names_show_as_text(tmp_path, monkeypatch):
    import sqlite3 as S
    import auth
    from app import app
    path = str(tmp_path / 'records.db'); _db(path)
    c = S.connect(path)
    c.execute("INSERT INTO result_rows VALUES (9, 1, 1, NULL, 'Autocross', 2025, '2025-05-25', 'ss', '2', 'ss', '8', 'Jordan', NULL, '46.0', NULL, NULL, 46.0, NULL, NULL, 11)")
    c.commit(); c.close()
    monkeypatch.setattr(records, 'PATH', path)
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Pat', 'level': 'official', 'roles': [], 'perms': set()} if e else None)
    app.config['TESTING'] = True
    cl = app.test_client()
    with cl.session_transaction() as s:
        s.update(authenticated=True, email='pat@example.org', who='Pat')
    r = cl.get('/records/event/1')
    assert r.status_code == 200 and b'Jordan' in r.data and b'/records/person/None' not in r.data


def _signed_in(monkeypatch, roles=(), email='pat@example.org'):
    import auth
    from app import app
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Pat Director', 'level': 'official', 'roles': list(roles), 'perms': set()} if e else None)
    app.config['TESTING'] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email=email, who='Pat Director')
    return c


def test_the_programs_director_corrects_records_and_corrections_survive_a_rebuild(tmp_path, monkeypatch):
    import officials as O
    path = str(tmp_path / 'records.db'); _db(path)
    monkeypatch.setattr(records, 'PATH', path)
    monkeypatch.setattr(O, 'director_email', lambda code: 'pat@example.org' if code == 'AX' else '')
    c = _signed_in(monkeypatch, roles=['Director'])
    assert b'Correct this event' in c.get('/records/event/1').data
    r = c.post('/records/event/1/edit', data={'name': 'Spring Opener', 'date': '2025-05-24', 'driver1': 'Alexis Example', 'class1': 'SS',
                                              'position1': '1', 'total1': '45.123'})
    assert r.status_code == 302
    page = c.get('/records/event/1').data
    assert b'Spring Opener' in page and b'Alexis Example' in page
    c.post('/records/autocross/2025/edit', data={'rm2': '1', 'new_class0': 'SS - Super Street', 'new_place0': '2', 'new_name0': 'Lee Sample', 'new_score0': '410'})
    season = c.get('/records/autocross/2025').data
    assert b'Season Opener Points' not in season and b'Lee Sample' in season
    # a rebuilt database arrives: the log is applied to it on the next page view
    import os
    import time
    os.remove(path); _db(path)
    os.utime(path, (time.time() + 5, time.time() + 5))
    page = c.get('/records/event/1').data
    assert b'Spring Opener' in page and b'Alexis Example' in page
    assert b'Lee Sample' in c.get('/records/autocross/2025').data


def test_only_the_programs_director_or_an_officer_corrects(tmp_path, monkeypatch):
    import officials as O
    path = str(tmp_path / 'records.db'); _db(path)
    monkeypatch.setattr(records, 'PATH', path)
    monkeypatch.setattr(O, 'director_email', lambda code: 'someone-else@example.org')
    c = _signed_in(monkeypatch, roles=['Director'])
    assert b'Correct this event' not in c.get('/records/event/1').data
    assert c.post('/records/event/1/edit', data={'name': 'Nope'}).status_code == 403
    assert c.get('/records/autocross/2025/edit').status_code == 403
    officer = _signed_in(monkeypatch, roles=['Treasurer'], email='t@example.org')
    assert officer.get('/records/autocross/2025/edit').status_code == 200
