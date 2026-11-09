"""Minutes: a failed conversion saves nothing (so a retry leaves no extra copies), and only a file in the drafts
folder can be published or deleted. Made-up names only."""
import io

import pytest

import minutes_logic as L


@pytest.fixture()
def site(monkeypatch):
    import app as A
    import auth
    import minutes
    calls = []
    monkeypatch.setenv('MINUTES_DRAFTS_FOLDER_ID', 'drafts')
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Sue Secretary', 'level': 'officer', 'roles': ['Secretary'],
                                                   'perms': {'minutes'}} if e else None)
    monkeypatch.setattr(minutes.S, 'enabled', lambda: True)
    monkeypatch.setattr(minutes.S, 'keep_original', lambda *a: calls.append('original'))
    monkeypatch.setattr(minutes.S, 'save_draft', lambda name, pdf: calls.append('draft') or ({'name': name}, False))
    monkeypatch.setattr(minutes.S, 'drafts', lambda: [{'id': 'draft-1', 'name': '2026-11 Board Minutes (draft).pdf'}])
    monkeypatch.setattr(minutes.S, 'is_draft', lambda fid: False)
    monkeypatch.setattr(minutes.S, 'publish_draft', lambda fid: calls.append(('publish', fid)) or ({'name': '2026-11 Board Minutes.pdf'}, False))
    monkeypatch.setattr(minutes.S, 'delete_draft', lambda fid: calls.append(('delete', fid)) or 'x.pdf')
    monkeypatch.setattr(minutes, 'log_audit_event', lambda *a, **k: None)
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email='sec@example.org', who='Sue Secretary')
    return c, calls, minutes


def _flashes(c):
    with c.session_transaction() as s:
        return ' | '.join(m for _, m in s.pop('_flashes', []))


def test_a_failed_conversion_saves_nothing(site, monkeypatch):
    c, calls, minutes = site

    def broken(data, name, parent):
        raise RuntimeError('Drive could not convert it')
    monkeypatch.setattr(minutes.S, 'to_pdf', broken)
    form = lambda: {'meeting': '2026-11-12', 'kind': 'Board meeting', 'file': (io.BytesIO(b'words'), 'November minutes.docx')}
    r = c.post('/minutes/', data=form(), content_type='multipart/form-data')
    assert r.status_code == 302 and calls == [] and 'nothing was saved' in _flashes(c)
    monkeypatch.setattr(minutes.S, 'to_pdf', lambda data, name, parent: b'%PDF')
    c.post('/minutes/', data=form(), content_type='multipart/form-data')
    assert calls == ['original', 'draft']                  # one original, kept only once the PDF exists


def test_only_a_draft_can_be_published_or_deleted(site):
    c, calls, minutes = site
    c.post('/minutes/publish', data={'id': 'some-other-drive-file'})
    c.post('/minutes/publish', data={'id': 'some-other-drive-file', 'action': 'delete'})
    assert calls == [] and 'no longer there' in _flashes(c)
    c.post('/minutes/publish', data={'id': 'draft-1'})
    assert calls == [('publish', 'draft-1')]


def test_store_refuses_to_publish_a_file_outside_the_drafts_folder(monkeypatch):
    import minutes_store as S
    monkeypatch.setenv('MINUTES_DRAFTS_FOLDER_ID', 'drafts')
    asked = []

    class Resp:
        ok = True

        def __init__(self, data): self.data = data
        def json(self): return self.data
        def raise_for_status(self): pass

    class Sess:
        def get(self, url, params=None, **k):
            asked.append(params)
            return Resp({'id': 'f', 'name': 'Payroll.pdf', 'parents': ['somewhere-else']})

        def patch(self, *a, **k):
            raise AssertionError('nothing may be changed')
        post = patch
    monkeypatch.setattr(S, '_s', lambda: Sess())
    monkeypatch.setattr(S, '_drive', lambda: ('https://drive.example/files', 'https://drive.example/upload'))
    with pytest.raises(ValueError):
        S.publish_draft('f')
    assert not S.is_draft('f') and all('alt' not in (p or {}) for p in asked)       # the file was never downloaded


def test_minutes_month_comes_from_the_file_name():
    assert L.month_of('2026-11 Board Minutes.pdf') == '2026-11' and L.month_of('2026-03 Board Minutes (special meeting).pdf') == '2026-03'
    assert L.month_of('2026 Budgets approved by email.pdf') is None and L.month_of('notes.pdf') is None
