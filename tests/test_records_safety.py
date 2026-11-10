"""Records safety: name and class checks before a correction is saved, before/after in the log and the audit, the
Corrections page, and Undo bringing the database back exactly. Made-up names only: the repo is public."""
import json
import os
import sqlite3
import time

import pytest
from werkzeug.datastructures import MultiDict

import audit_view
import records
import records_edits as RE
from test_records import _db, _signed_in


def _more(path):
    """A second event, a 'str' line, a one-word name, more standings and people."""
    c = sqlite3.connect(path)
    c.executescript('''
    INSERT INTO people VALUES (3, 'John Smith'), (4, 'Jon Smith'), (5, 'Kim Doe');
    INSERT INTO events VALUES (2, 'Autocross', 2025, '2025-06-15', 'Summer Points');
    INSERT INTO files VALUES (3, 2, 'Autocross', 2025, '2025-06-15', 'event', 'summer.pdf', 'https://drive.example/summer', 'Class results', 1, NULL);
    INSERT INTO result_rows VALUES (4, 3, 2, 4, 'Autocross', 2025, '2025-06-15', 'str', '1', 'str', '12', 'Jon Smith', NULL, '50.1', NULL, NULL, 50.1, NULL, NULL, 3);
    INSERT INTO result_rows VALUES (5, 3, 2, 5, 'Autocross', 2025, '2025-06-15', 'es', '1', 'es', '14', 'Kim Doe', NULL, '51.2', NULL, NULL, 51.2, NULL, NULL, 4);
    INSERT INTO result_rows VALUES (6, 1, 1, 3, 'Autocross', 2025, '2025-05-25', 'ss', '2', 'ss', '9', 'John Smith', NULL, '46.5', NULL, NULL, 46.5, NULL, NULL, 11);
    INSERT INTO season_standings VALUES (3, 'Autocross', 2025, 'STR', 1, 4, 'Jon Smith', 300, 2);
    INSERT INTO season_standings VALUES (4, 'Autocross', 2025, 'STR', 2, 5, 'Kim Doe', 250, 2);
    ''')
    c.commit(); c.close()


def _dump(path):
    c = sqlite3.connect(path)
    try:
        return {t: c.execute(f'select * from {t} order by id').fetchall() for t in ('events', 'files', 'people', 'result_rows', 'season_standings')}
    finally:
        c.close()


@pytest.fixture
def site(tmp_path, monkeypatch):
    import officials as O
    path = str(tmp_path / 'records.db'); _db(path); _more(path)
    monkeypatch.setattr(records, 'PATH', path)
    monkeypatch.setattr(O, 'director_email', lambda code: 'pat@example.org' if code == 'AX' else '')
    audit = []
    monkeypatch.setattr('utils.log_audit_event', lambda *a, **k: audit.append(a))
    return dict(path=path, tmp=tmp_path, audit=audit, ed=lambda: _signed_in(monkeypatch, roles=['Director']),
                vol=lambda: _signed_in(monkeypatch, roles=[], email='vol@example.org'))


def _log(site):
    return json.load(open(site['tmp'] / 'records-edits.json'))


def _post(c, url, form, ok=()):
    return c.post(url, data=MultiDict(list(form.items()) + [('ok', t) for t in ok]))


# ---- name and class warnings -----------------------------------------------------------------------------------

def test_a_new_name_needs_a_confirm_and_says_so(site):
    c = site['ed']()
    before = _dump(site['path'])
    r = c.post('/records/event/2/edit', data={'driver4': 'Jonny Smithe'})
    html = r.data.decode()
    assert r.status_code == 200 and 'This creates a new person' in html and 'Save anyway' in html
    assert 'name="ok" value="driver4=Jonny Smithe"' in html
    assert _dump(site['path']) == before and not os.path.exists(site['tmp'] / 'records-edits.json')      # nothing saved
    assert _post(c, '/records/event/2/edit', {'driver4': 'Jonny Smithe'}, ['driver4=Jonny Smithe']).status_code == 302
    assert _log(site)[0]['set'] == {'driver': 'Jonny Smithe'} and _log(site)[0]['was'] == {'driver': 'Jon Smith'}


def test_joining_another_person_names_them_and_their_events(site):
    c = site['ed']()
    r = c.post('/records/event/2/edit', data={'driver4': 'john smith'})
    assert "This joins John Smith&#39;s results (1 event)" in r.data.decode()
    r = c.post('/records/autocross/2025/edit', data={'name3': 'John Smith'})
    assert "This joins John Smith&#39;s results (1 event)" in r.data.decode() and not os.path.exists(site['tmp'] / 'records-edits.json')


def test_a_one_word_name_is_refused_even_with_a_confirm(site):
    c = site['ed']()
    r = _post(c, '/records/event/2/edit', {'driver4': 'Jon'}, ['driver4=Jon'])
    assert r.status_code == 200 and 'Use a first and last name' in r.data.decode()
    r = _post(c, '/records/autocross/2025/edit', {'new_name0': 'Prince', 'new_place0': '3', 'new_class0': 'STR'}, ['new_name0=Prince'])
    assert 'Use a first and last name' in r.data.decode()
    assert not os.path.exists(site['tmp'] / 'records-edits.json')


def test_a_case_only_fix_of_a_name_goes_through_without_asking(site):
    c = site['ed']()
    assert c.post('/records/event/2/edit', data={'driver4': 'JON SMITH'}).status_code == 302


def test_class_near_duplicate_refused_and_new_class_needs_a_confirm(site):
    c = site['ed']()
    r = _post(c, '/records/autocross/2025/edit', {'class4': 'S tr'}, ['class4=S tr'])
    assert r.status_code == 200 and 'Did you mean STR?' in r.data.decode()
    r = c.post('/records/autocross/2025/edit', data={'class4': 'XP'})
    assert 'XP is not a class in 2025 Autocross yet' in r.data.decode()
    assert not os.path.exists(site['tmp'] / 'records-edits.json')
    assert _post(c, '/records/autocross/2025/edit', {'class4': 'XP'}, ['class4=XP']).status_code == 302
    # an add-standing row is checked the same way
    r = _post(c, '/records/autocross/2025/edit', {'new_name0': 'Kim Doe', 'new_place0': '3', 'new_class0': 'str'}, ['new_name0=Kim Doe'])
    assert 'Did you mean STR?' in r.data.decode()
    r = c.post('/records/autocross/2025/edit', data={'new_name0': 'Lee Sample', 'new_class0': 'STR'})
    assert 'Give the place as a number' in r.data.decode()


def test_near_duplicates_count_what_the_form_itself_changes(site):
    c = site['ed']()
    r = c.post('/records/event/2/edit', data={'class4': 'STR'})        # the only 'str' line, retyped: a new class, not a near-duplicate
    assert 'STR is not a class in 2025 Autocross yet' in r.data.decode() and 'Did you mean str?' not in r.data.decode()
    r = c.post('/records/event/2/edit', data={'class5': 'STR '})       # while another line keeps 'str'
    assert r.status_code == 200 and 'Did you mean str?' in r.data.decode()
    r = c.post('/records/event/2/edit', data={'class5': 'str'})        # used by another line already: saved, no question
    assert r.status_code == 302
    r = c.post('/records/autocross/2025/edit', data={'class3': 'str', 'class4': 'str'})     # a whole class retyped at once
    assert 'str is not a class in 2025 Autocross yet' in r.data.decode() and 'Did you mean' not in r.data.decode().split('<script>')[0]


def test_forms_offer_classes_and_the_name_endpoints(site):
    c = site['ed']()
    html = c.get('/records/autocross/2025/edit').data.decode()
    assert '<option value="STR" data-n="2">' in html and 'data-check="name"' in html and 'list="rec-names"' in html
    html = c.get('/records/event/2/edit').data.decode()
    assert '<option value="str" data-n="1">' in html and '<option value="es" data-n="1">' in html
    names = c.get('/records/names.json?program=Autocross').get_json()
    assert ['John Smith', 1] in names and all(len(n.split()) >= 2 for n, _ in names)
    assert c.get('/records/name.json?name=jon').get_json() == {'stop': 'Use a first and last name'}
    assert c.get('/records/name.json?name=kim  doe').get_json()['joins'] == 'Kim Doe'
    assert c.get('/records/name.json?name=New Person').get_json()['new'] is True


# ---- before and after: the log, the audit, the Corrections page -------------------------------------------------

def test_log_and_audit_carry_before_and_after(site):
    c = site['ed']()
    _post(c, '/records/autocross/2025/edit', {'name3': 'John Smith', 'class4': 'XP'}, ['name3=John Smith', 'class4=XP'])
    log = _log(site)
    assert {json.dumps(e['was'], sort_keys=True) for e in log} == {'{"name": "Jon Smith"}', '{"class": "STR"}'}
    assert all(e.get('id') for e in log)
    a, who, d = site['audit'][-1]
    assert a == 'RECORDS_CORRECTED' and set(d['diff']) == {'Jon Smith -> John Smith', 'class STR -> XP'}
    line = audit_view.plain({'action': a, 'user': who, 'details': d})
    assert line.startswith('Pat Director corrected the 2025 Autocross standings: ') and 'Jon Smith -> John Smith' in line
    old = audit_view.plain({'action': 'RECORDS_CORRECTED', 'user': 'Lee', 'details': {'what': 'standings Autocross 2025', 'changes': 2}})
    assert old == 'Lee corrected the records: standings Autocross 2025 (2 changes)'


def test_corrections_page_shows_new_and_old_entries(site):
    old = [{'kind': 'row', 'key': {'url': 'https://drive.example/summer', 'line': 3, 'driver': 'Jon Smith'}, 'set': {'driver': 'Jonathan Smith'},
            'who': 'Lee Sample', 'when': '2026-10-01T10:00:00'},
           {'kind': 'standing', 'key': {'program': 'Autocross', 'year': 2025, 'class': 'STR', 'name': 'Kim Doe', 'place': 2, 'score': 250.0},
            'set': {'class': 'ES'}, 'who': 'Lee Sample', 'when': '2026-10-02T10:00:00'},
           {'kind': 'event', 'key': {'url': 'https://drive.example/summer'}, 'set': {'name': 'Summer Points II'}, 'who': 'Lee Sample',
            'when': '2026-10-03T10:00:00'}]
    RE.save(str(site['tmp'] / 'records-edits.json'), old)
    c = site['ed']()
    c.get('/records/')                                  # the log is applied (a "rebuilt" database meets the log)
    _post(c, '/records/event/1/edit', {'class6': 'es'})
    _post(c, '/records/autocross/2025/edit', {'new_name0': 'Pat Example', 'new_place0': '3', 'new_class0': 'ES'}, ['new_name0=Pat Example'])
    html = c.get('/records/corrections').data.decode()
    for s in ('Driver: Jon Smith -&gt; Jonathan Smith', 'Class: STR -&gt; ES', 'Event name: Summer Points II (before: not recorded)',
              'Class: ss -&gt; es', 'Added Pat Example, 3rd in ES', 'Oct 1, 2026', 'Lee Sample', 'Pat Director', '2025 standings, STR'):
        assert s in html, s
    assert html.index('Added Pat Example') < html.index('Class: ss -&gt; es') < html.index('Summer Points II (before')    # newest first
    assert html.count('>Undo</button>') == 5
    assert b'See every correction' in c.get('/records/').data
    vol = site['vol']()
    page = vol.get('/records/corrections')
    assert page.status_code == 200 and b'Class: STR -&gt; ES' in page.data and b'>Undo</button>' not in page.data
    assert b'See every correction' not in vol.get('/records/').data
    ref = RE.ref(_log(site)[0])
    assert vol.post('/records/corrections/undo', data={'ref': ref}).status_code == 403


# ---- undo -------------------------------------------------------------------------------------------------------

def _undo(c, site, n=-1):
    ref = RE.ref(_log(site)[n])
    assert c.post('/records/corrections/undo', data={'ref': ref}).status_code == 302
    return ref


@pytest.mark.parametrize('url,form,ok', [
    ('/records/event/2/edit', {'driver4': 'Jonny Smithe'}, ['driver4=Jonny Smithe']),             # a rename (makes a new person)
    ('/records/event/2/edit', {'class5': 'str'}, []),                                             # a class change
    ('/records/event/2/edit', {'rm5': '1'}, []),                                                  # a line removed
    ('/records/autocross/2025/edit', {'rm4': '1'}, []),                                           # a standing removed
    ('/records/autocross/2025/edit', {'new_name0': 'Pat Example', 'new_place0': '3', 'new_class0': 'STR', 'new_score0': '200'}, ['new_name0=Pat Example']),
    ('/records/event/2/edit', {'remove_event': '1'}, []),                                         # an event removed
    ('/records/event/2/edit', {'name': 'Summer Final', 'date': '2025-06-16'}, []),
])
def test_undo_brings_the_database_back_exactly(site, url, form, ok):
    c = site['ed']()
    c.get('/records/')
    before = _dump(site['path'])
    assert _post(c, url, form, ok).status_code == 302
    assert _dump(site['path']) != before
    _undo(c, site)
    assert _dump(site['path']) == before and _log(site) == []
    a, who, d = site['audit'][-1]
    assert a == 'RECORDS_UNDONE' and d['restored'] is True and d['made_by'] == 'Pat Director'
    assert audit_view.plain({'action': a, 'user': who, 'details': d}).startswith('Pat Director undid a correction to ')


def test_undo_of_one_entry_leaves_later_ones_applied(site):
    c = site['ed']()
    c.get('/records/')
    start = _dump(site['path'])
    _post(c, '/records/autocross/2025/edit', {'class4': 'XP'}, ['class4=XP'])
    _post(c, '/records/event/2/edit', {'class5': 'str'}, [])
    _post(c, '/records/autocross/2025/edit', {'rm3': '1'}, [])
    first = RE.ref(_log(site)[0])
    assert c.post('/records/corrections/undo', data={'ref': first}).status_code == 302
    now = _dump(site['path'])
    assert [r for r in now['season_standings'] if r[0] == 4][0][3] == 'STR'          # the undone class change
    assert [r for r in now['result_rows'] if r[0] == 5][0][9] == 'str'               # later ones kept
    assert not any(r[0] == 3 for r in now['season_standings'])
    assert len(_log(site)) == 2 and now != start


def test_undo_waits_for_a_later_correction_on_the_same_line(site):
    c = site['ed']()
    _post(c, '/records/event/2/edit', {'driver4': 'Jonny Smithe'}, ['driver4=Jonny Smithe'])
    _post(c, '/records/event/2/edit', {'class4': 'es'}, [])
    c.post('/records/corrections/undo', data={'ref': RE.ref(_log(site)[0])})
    with c.session_transaction() as s:
        assert 'Undo that one first' in ' '.join(m for _, m in s.get('_flashes', []))
    assert len(_log(site)) == 2


def test_undo_after_a_rebuild_that_already_holds_the_corrections(site):
    """A rebuild applies the log it was given, so the database that arrives may hold the corrections: a rename is put
    back from its old value; a removed event can only come back with the next rebuild."""
    c = site['ed']()
    c.get('/records/')
    pristine = _dump(site['path'])
    _post(c, '/records/event/2/edit', {'driver4': 'Jonny Smithe'}, ['driver4=Jonny Smithe'])
    _post(c, '/records/event/1/edit', {'remove_event': '1'}, [])
    log = _log(site)
    path = site['path']
    os.remove(path); _db(path); _more(path)                 # the rebuild, with the log applied as export_records.py does
    x = sqlite3.connect(path); RE.apply_all(x, log); x.commit(); x.close()
    os.utime(path, (time.time() + 5, time.time() + 5))
    html = c.get('/records/corrections').data.decode()
    assert html.count('comes back with the next results rebuild') == 2       # the note and the confirm of the removed event
    _undo(c, site, 0)                                                        # the rename
    rows = {r[0]: r for r in _dump(path)['result_rows']}
    assert rows[4][11] == 'Jon Smith' and rows[4][3] == 4
    _undo(c, site, 0)                                                        # the removed event
    assert site['audit'][-1][2]['restored'] is False and _log(site) == []
    assert 'comes back with the next results rebuild' in audit_view.plain(dict(action='RECORDS_UNDONE', user='Pat', details=site['audit'][-1][2]))
    os.remove(path); _db(path); _more(path)                  # the next rebuild, from the log as it is now (empty)
    os.utime(path, (time.time() + 10, time.time() + 10))
    c.get('/records/')
    assert _dump(path) == pristine


def test_the_base_copy_is_kept_before_the_first_correction_and_refreshed_by_a_rebuild(site):
    c = site['ed']()
    base = site['tmp'] / 'records-base.db'
    pristine = _dump(site['path'])
    _post(c, '/records/event/2/edit', {'class5': 'str'}, [])
    assert _dump(str(base)) == pristine
    path = site['path']
    os.remove(path); _db(path)                                # a rebuild with less in it
    os.utime(path, (time.time() + 5, time.time() + 5))
    c.get('/records/')
    assert len(_dump(str(base))['result_rows']) == 1


def test_undo_writes_the_log_under_the_lock(site, monkeypatch):
    c = site['ed']()
    _post(c, '/records/event/2/edit', {'class5': 'str'}, [])
    held, real = [], RE.save

    def save(p, edits):
        from filelock import FileLock, Timeout
        try:
            FileLock(p + '.lock').acquire(timeout=0)
            held.append(False)
        except Timeout:
            held.append(True)
        real(p, edits)
    monkeypatch.setattr(RE, 'save', save)
    _undo(c, site)
    assert held == [True]


def test_undo_of_an_entry_already_gone(site):
    c = site['ed']()
    r = c.post('/records/corrections/undo', data={'ref': 'nope'})
    assert r.status_code == 302
    with c.session_transaction() as s:
        assert 'already undone' in ' '.join(m for _, m in s.get('_flashes', []))


def test_reverse_and_depends_rules():
    row = {'kind': 'row', 'key': {'url': 'u', 'line': 3, 'driver': 'Jon Smith'}, 'set': {'driver': 'John Smith', 'class': 'STR'}, 'was': {'class': 'str'}}
    assert RE.reverse(row) == [{'kind': 'row', 'key': {'url': 'u', 'line': 3, 'driver': 'John Smith'}, 'set': {'driver': 'Jon Smith', 'class': 'str'}}]
    assert RE.reverse({'kind': 'event', 'key': {'url': 'u'}, 'remove': True}) == []
    assert RE.depends(row, {'kind': 'row', 'key': {'url': 'u', 'line': 3, 'driver': 'John Smith'}, 'set': {'total': '1'}})
    assert not RE.depends(row, {'kind': 'row', 'key': {'url': 'u', 'line': 4, 'driver': 'John Smith'}, 'set': {'total': '1'}})
    assert RE.norm_class(' S tr') == RE.norm_class('STR')
