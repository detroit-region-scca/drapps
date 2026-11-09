"""Records: a standings correction changes the one row it was made on, the corrections log is kept under a lock, and
a results upload that fails part way says which files were saved. Made-up names only."""
import io
import json
import sqlite3

import records
import records_edits as RE
from test_records import _db, _signed_in


def _twins(path):
    """The same name twice in one class (a sheet that was read twice, or two drivers of one name)."""
    c = sqlite3.connect(path)
    c.executescript('''
    INSERT INTO people VALUES (3, 'Kim Doe');
    INSERT INTO season_standings VALUES (3, 'Autocross', 2025, 'ES', 1, 3, 'Kim Doe', 300, 2);
    INSERT INTO season_standings VALUES (4, 'Autocross', 2025, 'ES', 4, 3, 'Kim Doe', 120, 2);
    INSERT INTO season_standings VALUES (5, 'Autocross', 2025, 'ES', 6, 3, 'Kim Doe', 80, 2);
    INSERT INTO season_standings VALUES (6, 'Autocross', 2025, 'ES', 6, 3, 'Kim Doe', 80, 2);
    ''')
    c.commit(); c.close()


def _rows(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("select id, place, score from season_standings where class = 'ES' order by id").fetchall()
    finally:
        c.close()


def test_a_standings_correction_changes_only_its_own_row(tmp_path):
    path = str(tmp_path / 'records.db'); _db(path); _twins(path)
    key = {'program': 'Autocross', 'year': 2025, 'class': 'ES', 'name': 'Kim Doe'}
    c = sqlite3.connect(path)
    assert RE.apply(c, {'kind': 'standing', 'key': dict(key, place=4, score=120.0), 'set': {'score': '125'}}) == 1
    assert RE.apply(c, {'kind': 'standing', 'key': dict(key, place=6, score=80.0, nth=1), 'remove': True}) == 1   # one of two alike
    c.commit(); c.close()
    assert _rows(path) == [(3, 1, 300.0), (4, 4, 125.0), (5, 6, 80.0)]
    c = sqlite3.connect(path)
    assert RE.apply(c, {'kind': 'standing', 'key': key, 'set': {'score': '1'}}) == 3      # an entry logged before: as it always worked
    assert RE.apply(c, {'kind': 'standing', 'key': dict(key, place=9), 'set': {'score': '2'}}) == 0
    c.close()


def test_correcting_one_of_two_same_named_rows_through_the_page(tmp_path, monkeypatch):
    path = str(tmp_path / 'records.db'); _db(path); _twins(path)
    monkeypatch.setattr(records, 'PATH', path)
    monkeypatch.setattr(records, 'can_edit', lambda program: True)
    c = _signed_in(monkeypatch, roles=['Treasurer'])
    form = {f'{fld}{sid}': v for sid, pl, sc in _rows(path) for fld, v in (('class', 'ES'), ('place', str(pl)), ('name', 'Kim Doe'), ('score', '%g' % sc))}
    form.update(score4='125', rm6='1')                   # fix the fourth-place score; remove the second of the two alike
    assert c.post('/records/autocross/2025/edit', data=form).status_code == 302
    assert _rows(path) == [(3, 1, 300.0), (4, 4, 125.0), (5, 6, 80.0)]
    log = json.load(open(tmp_path / 'records-edits.json'))
    assert [e['key'].get('nth') for e in log] == [1, 0] and all(e['who'] == 'Pat Director' for e in log)
    # the log holds every correction, applied again to a rebuilt database
    import os
    import time
    os.remove(path); _db(path); _twins(path)
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert c.get('/records/autocross/2025').status_code == 200
    assert _rows(path) == [(3, 1, 300.0), (4, 4, 125.0), (5, 6, 80.0)]


def test_corrections_log_is_written_under_a_file_lock(tmp_path, monkeypatch):
    path = str(tmp_path / 'records.db'); _db(path)
    monkeypatch.setattr(records, 'PATH', path)
    held = []
    real_save = RE.save

    def save(p, edits):
        from filelock import FileLock, Timeout
        try:                       # what another worker would find while this one writes the log
            FileLock(p + '.lock').acquire(timeout=0)
            held.append(False)
        except Timeout:
            held.append(True)
        real_save(p, edits)
    monkeypatch.setattr(RE, 'save', save)
    monkeypatch.setattr(records, 'can_edit', lambda program: True)
    c = _signed_in(monkeypatch, roles=['Treasurer'])
    c.post('/records/autocross/2025/edit', data={'rm2': '1'})
    assert held == [True] and len(json.load(open(tmp_path / 'records-edits.json'))) == 1


def test_upload_that_fails_part_way_says_which_files_were_saved(tmp_path, monkeypatch):
    import auth
    import storage
    from app import app
    path = str(tmp_path / 'records.db'); _db(path)
    monkeypatch.setattr(records, 'PATH', path)
    monkeypatch.setattr(records, 'INBOX', 'inbox-folder')
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Vol', 'level': 'volunteer', 'roles': [], 'perms': set()} if e else None)
    audit = []
    monkeypatch.setattr('utils.log_audit_event', lambda *a, **k: audit.append(a))

    class Sess:
        n = 0

        def post(self, url, params=None, files=None, **k):
            Sess.n += 1
            if Sess.n >= 2:
                raise ConnectionError('Drive did not answer')

            class R:
                def raise_for_status(self): pass
            return R()

        def get(self, url, params=None, **k):
            class R:
                def raise_for_status(self): pass
                def json(self): return {'files': []}
            return R()

    class Client:
        class http_client:
            session = Sess()
    monkeypatch.setattr(storage, 'sheets_client', lambda: Client)
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='vol@example.org', who='Vol', _csrf='t')
    r = c.post('/records/add', data={'_csrf': 't', 'program': 'Autocross', 'date': '2026-11-14', 'event': 'Turkey Trot',
                                     'files': [(io.BytesIO(b'%PDF-1.4'), 'class.pdf'), (io.BytesIO(b'%PDF-1.4'), 'pax.pdf')]},
               content_type='multipart/form-data')
    assert r.status_code == 302
    with c.session_transaction() as s:
        said = ' | '.join(m for _, m in s.get('_flashes', []))
    assert 'Saved 1 of 2 files (class.pdf)' in said and 'Not saved: pax.pdf' in said
    assert audit and audit[0][2]['files'] == ['class.pdf']
