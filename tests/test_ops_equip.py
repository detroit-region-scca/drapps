"""Operations: Done marks a to-do once and moves it past today, a row deleted from the sheet gives a plain message,
finished one-time to-dos leave "No date yet", and a document upload that fails part way says so. Made-up names only."""
from datetime import date, timedelta

import pytest

import equip_store as E

TODAY = date.today().isoformat()


def _task(**kw):
    return dict({'id': 'TK-001', 'task': 'Renew trailer plate', 'about': '', 'due': '2024-03-10', 'repeat_months': 12.0, 'cost': None, 'owner': '',
                 'notes': '', 'last_done': '', 'done_by': '', 'category': '', 'paid_how': '', 'auto': '', 'qbo_check': '', 'account': '', 'ends': ''}, **kw)


@pytest.fixture()
def site(monkeypatch):
    import app as A
    import auth
    import equip
    state = {'tasks': [_task()], 'saves': [], 'fail': None}
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Ed Gear', 'level': 'official', 'roles': ['Equipment'],
                                                   'perms': {'ops'}} if e else None)
    monkeypatch.setattr(equip, 'editors', lambda: {'ed@example.org': 'Ed Gear'})
    monkeypatch.setattr(equip.E, 'enabled', lambda: True)
    monkeypatch.setattr(equip.E, 'tasks', lambda: [dict(t) for t in state['tasks']])

    def save(tab, row, changed=None, expect=None):
        if state['fail']:
            raise state['fail']
        state['saves'].append((dict(row), expect))
        state['tasks'] = [dict(row) if t['id'] == row['id'] else t for t in state['tasks']]
        return row['id']
    monkeypatch.setattr(equip.E, 'save', save)
    monkeypatch.setattr(equip, 'log_audit_event', lambda *a, **k: None)
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='ed@example.org', who='Ed Gear', equip_editor={'email': 'ed@example.org', 'name': 'Ed Gear'})
    return c, state


def _flashes(c):
    with c.session_transaction() as s:
        return ' | '.join(m for _, m in s.pop('_flashes', []))


def test_done_moves_a_long_overdue_to_do_past_today_and_only_once(site):
    c, state = site
    c.post('/operations/tasks/TK-001', data={'action': 'done', 'was_due': '2024-03-10'})
    row, expect = state['saves'][0]
    assert row['due'] > TODAY and row['due'][5:] == '03-10' and row['last_done'] == TODAY     # not 2025-03-10, still overdue
    assert expect == {'due': '2024-03-10', 'last_done': ''}                                   # checked again in the sheet, under the lock
    c.post('/operations/tasks/TK-001', data={'action': 'done', 'was_due': '2024-03-10'})      # the double click
    assert len(state['saves']) == 1 and 'already marked done' in _flashes(c)
    c.post('/operations/tasks/TK-001', data={'action': 'done'})                               # an old page, same day
    assert len(state['saves']) == 1


def test_done_gives_way_when_the_sheet_changed_under_it(site):
    c, state = site
    state['fail'] = E.Changed('TK-001')                      # the other worker saved between this one's read and its write
    assert c.post('/operations/tasks/TK-001', data={'action': 'done', 'was_due': '2024-03-10'}).status_code == 302
    assert 'already marked done' in _flashes(c)


def test_a_row_deleted_from_the_sheet_is_a_message_not_an_error(site):
    c, state = site
    state['fail'] = E.Gone('TK-001')
    r = c.post('/operations/tasks/TK-001', data={'action': 'done', 'was_due': '2024-03-10'})
    assert r.status_code == 302 and 'no longer in the Operations sheet' in _flashes(c)
    r = c.post('/operations/tasks/TK-001', data={'task': 'Renew trailer plate', 'due': '2027-03-10'})
    assert r.status_code == 302 and 'nothing was saved' in _flashes(c)


class Sheet:
    def __init__(self, ids, live):
        self.ids, self.live, self.written = ids, live, []

    def col_values(self, n):
        return list(self.ids)

    def get(self, rng, **k):
        return [list(self.live)]

    def update(self, values, where, **k):
        self.written.append((where, values))


def test_store_save_refuses_a_missing_row_and_a_row_that_changed(monkeypatch, tmp_path):
    monkeypatch.setattr(E, 'LOCK', str(tmp_path / 'equipment.lock'))
    monkeypatch.setattr(E, 'refresh', lambda: None)
    tab = 'Recurring costs and to-dos'
    ws = Sheet(['ID', 'TK-001'], ['TK-001', 'Renew trailer plate', '', 46356, 12, '', '', '', '', ''])   # due typed by hand: a day number
    monkeypatch.setattr(E, '_ws', lambda title: ws)
    with pytest.raises(E.Gone):
        E.save(tab, _task(id='TK-404'), changed={'due'})
    with pytest.raises(E.Changed):
        E.save(tab, _task(due='2027-11-30'), changed={'due'}, expect={'due': '2025-11-30', 'last_done': ''})
    assert ws.written == []
    E.save(tab, _task(due='2027-11-30', last_done=TODAY), changed={'due', 'last_done'}, expect={'due': '2026-11-30', 'last_done': ''})
    where, values = ws.written[0]
    assert where == 'A2' and values[0][3] == '2027-11-30' and values[0][8] == TODAY and values[0][1] == 'Renew trailer plate'


def test_finished_one_time_to_dos_leave_no_date_yet(site):
    import equip
    c, state = site
    soon = (date.today() + timedelta(days=5)).isoformat()
    state['tasks'] = [_task(id='TK-001', task='Fix the canopy', due='', repeat_months=None, last_done='2026-10-01'),
                      _task(id='TK-002', task='Find the spare key', due='', repeat_months=None),
                      _task(id='TK-003', task='Renew trailer plate', due=soon)]
    assert [equip._finished(t) for t in state['tasks']] == [True, False, False]
    page = c.get('/operations/tasks').get_data(as_text=True)
    assert page.index('No date yet (1)') < page.index('Find the spare key') < page.index('Done (1)') < page.index('Fix the canopy')


def test_report_leaves_finished_one_time_to_dos_out_of_no_date_yet():
    import equip_pdf
    tasks = [_task(id='TK-001', task='Fix the canopy', due='', repeat_months=None, last_done='2026-10-01'),
             _task(id='TK-002', task='Find the spare key', due='', repeat_months=None)]
    assert equip_pdf.build([], [], tasks, today=date(2026, 11, 9))[:4] == b'%PDF'
    assert equip_pdf._roll({'next': '2026-01-31', 'every': 1}, '2026-11-09') == '2026-11-30'      # month end stays month end
    assert equip_pdf._roll({'next': '2026-12-01', 'every': 12}, '2026-11-09') == '2026-12-01'


def test_document_upload_that_fails_part_way_says_which_files_were_saved(site, monkeypatch):
    import io
    import equip
    import equip_docs as D
    c, state = site
    monkeypatch.setattr(equip.E, 'documents', lambda: [{'id': 'DC-001', 'for_id': 'EQ-001', 'for_name': 'Race trailer',
                                                        'folder': 'https://drive.google.com/drive/folders/abc123', 'needed': 'Title', 'notes': ''}])
    done = []

    def upload(folder, name, f, who):
        if done:
            raise ConnectionError('Drive did not answer')
        done.append(name)
    monkeypatch.setattr(D, 'upload', upload)
    monkeypatch.setattr(D, 'refresh', lambda: None)
    r = c.post('/operations/documents/add', data={'doc': 'Title', 'row_id': 'DC-001',
                                                  'files': [(io.BytesIO(b'a'), 'front.jpg'), (io.BytesIO(b'b'), 'back.jpg')]},
               content_type='multipart/form-data')
    assert r.status_code == 302 and len(done) == 1
    said = _flashes(c)
    assert 'Saved 1 of 2 files for Race trailer' in said and 'Not saved: back.jpg' in said
