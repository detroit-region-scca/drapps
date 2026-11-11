"""Hassles: anyone signed in posts, gives thumbs up (not on their own) and replies; the admin sets the status; admin@
hears about new hassles and replies. The sheet is replaced by an in-memory stand-in."""
import pytest


class FakeStore:
    STATUSES = ['Open', 'Planned', 'Fixed', 'Not doing']

    def __init__(self):
        self.rows, self.replies, self.n = [], [], 0

    def enabled(self):
        return True

    def hassles(self):
        out = []
        for h in self.rows:
            h = dict(h, votes=list(h['votes']))
            h['replies'] = [dict(r) for r in self.replies if r['hassle_id'] == h['id']]
            out.append(h)
        return out

    def get(self, hid):
        return next((h for h in self.hassles() if h['id'] == hid), None)

    def add(self, title, details, by, by_email):
        self.n += 1
        self.rows.append({'id': self.n, 'created': '2026-11-11', 'by': by, 'by_email': by_email, 'title': title,
                          'details': details, 'status': 'Open', 'note': '', 'votes': []})
        return self.n

    def _h(self, hid):
        return next((h for h in self.rows if h['id'] == hid), None)

    def update(self, hid, **f):
        h = self._h(hid)
        if h:
            h.update(f)
        return bool(h)

    def toggle_vote(self, hid, email):
        v = self._h(hid)['votes']
        v.remove(email) if email in v else v.append(email)
        return True

    def delete(self, hid):
        self.rows = [h for h in self.rows if h['id'] != hid]
        self.replies = [r for r in self.replies if r['hassle_id'] != hid]

    def reply(self, hid, text, by, by_email):
        self.replies.append({'hassle_id': hid, 'at': f'2026-11-11 09:0{len(self.replies)}', 'by': by, 'by_email': by_email, 'text': text})

    def delete_reply(self, hid, at, by_email):
        self.replies = [r for r in self.replies if not (r['hassle_id'] == hid and r['at'] == at and r['by_email'] == by_email)]


@pytest.fixture()
def box(monkeypatch):
    import auth
    import hassles
    people = {'pat@example.org': {'name': 'Pat', 'level': 'official', 'roles': ['Director'], 'perms': {'events', 'hassles_post'}},
              'vic@example.org': {'name': 'Vic', 'level': 'volunteer', 'roles': [], 'perms': {'invoices', 'hassles_post'}},
              'ada@example.org': {'name': 'Ada', 'level': 'officer', 'roles': ['Treasurer'], 'perms': {'hassles', 'hassles_post'}}}
    monkeypatch.setattr(auth, 'lookup', lambda e: dict(people[e], email=e) if e in people else None)
    store, sent = FakeStore(), []
    monkeypatch.setattr(hassles, 'S', store)
    monkeypatch.setattr(hassles, 'log_audit_event', lambda *a, **k: None)
    monkeypatch.setattr(hassles, 'send_mail', lambda subject, body, to, **k: sent.append((subject, body, to)))
    from app import app
    app.config['TESTING'] = True

    def as_(email):
        c = app.test_client()
        with c.session_transaction() as s:
            s.update(authenticated=True, email=email, who=people[email]['name'])
        return c
    return as_, store, sent


def test_post_vote_and_status(box):
    as_, S, sent = box
    pat, vic, ada = as_('pat@example.org'), as_('vic@example.org'), as_('ada@example.org')
    pat.post('/hassles/new', data={'title': 'Dark mode', 'details': 'Easier at night'})
    vic.post('/hassles/new', data={'title': 'Event map'})
    assert sent[0][0] == 'New hassle: Dark mode' and 'Pat posted' in sent[0][1] and sent[0][2] == 'admin@drscca.org'
    pat.post('/hassles/1/vote')                      # not on your own
    vic.post('/hassles/1/vote')                      # a volunteer can vote
    ada.post('/hassles/1/vote')
    ada.post('/hassles/1/vote')                      # pressed again: taken back
    assert S.get(1)['votes'] == ['vic@example.org']
    page = vic.get('/hassles/').get_data(as_text=True)
    assert page.index('Dark mode') < page.index('Event map') and 'Pat' in page
    pat.post('/hassles/1/status', data={'status': 'Fixed', 'note': 'x'})
    assert S.get(1)['status'] == 'Open'              # only the admin
    ada.post('/hassles/1/status', data={'status': 'Planned', 'note': 'Next month'})
    assert S.get(1)['status'] == 'Planned' and 'Next month' in vic.get('/hassles/').get_data(as_text=True)


def test_anyone_can_reply_and_delete_their_own_reply(box):
    as_, S, sent = box
    pat, vic, ada = as_('pat@example.org'), as_('vic@example.org'), as_('ada@example.org')
    pat.post('/hassles/new', data={'title': 'Dark mode'})
    vic.post('/hassles/1/reply', data={'text': 'Me too, my phone is blinding'})
    ada.post('/hassles/1/reply', data={'text': 'On the list'})
    assert [r['by'] for r in S.get(1)['replies']] == ['Vic', 'Ada']
    assert sent[-1][0] == 'Reply on hassle: Dark mode' and 'Vic replied' in sent[-1][1]      # the admin's own reply sends nothing
    page = pat.get('/hassles/').get_data(as_text=True)
    assert 'my phone is blinding' in page and '2 replies' in page
    r = S.get(1)['replies'][0]
    pat.post('/hassles/1/reply/delete', data={'at': r['at'], 'by_email': r['by_email']})
    assert len(S.get(1)['replies']) == 2             # not someone else's
    vic.post('/hassles/1/reply/delete', data={'at': r['at'], 'by_email': r['by_email']})
    assert [x['by'] for x in S.get(1)['replies']] == ['Ada']


def test_delete_is_for_the_poster_or_the_admin(box):
    as_, S, _ = box
    as_('pat@example.org').post('/hassles/new', data={'title': 'Dark mode'})
    as_('vic@example.org').post('/hassles/1/delete')
    assert len(S.hassles()) == 1
    as_('pat@example.org').post('/hassles/1/delete')
    assert S.hassles() == []
    as_('pat@example.org').post('/hassles/new', data={'title': ''})
    assert S.hassles() == []                         # a title is required


def test_store_reads_the_sheet(monkeypatch):
    import hassles_store as HS
    res = {'valueRanges': [
        {'values': [HS.COLS, ['1', '2026-11-11', 'Pat', 'Pat@Example.org', 'Dark mode', '', 'Planned', 'soon', 'a@x.org\nb@x.org'],
                    ['', 'blank row'], ['2', '2026-11-11', 'Vic', 'vic@example.org', 'Map', '', 'weird', '', '']]},
        {'values': [HS.RCOLS, ['1', '2026-11-11 10:00', 'Vic', 'vic@example.org', 'later'], ['1', '2026-11-11 09:00', 'Ada', 'ada@x.org', 'first'],
                    ['9', '2026-11-11 09:00', 'X', 'x@x.org', 'orphan']]}]}
    monkeypatch.setattr(HS, '_sheet', lambda: 'sheet')
    monkeypatch.setattr(HS.C, 'batch_get', lambda sid, ranges, params=None: res)
    hs = HS.hassles()
    assert [h['id'] for h in hs] == [1, 2] and hs[0]['votes'] == ['a@x.org', 'b@x.org'] and hs[0]['by_email'] == 'pat@example.org'
    assert hs[1]['status'] == 'Open' and [r['text'] for r in hs[0]['replies']] == ['first', 'later'] and hs[1]['replies'] == []


def test_the_permission_switches_hassles_off(box):
    import auth
    as_, S, _ = box
    as_('pat@example.org').post('/hassles/new', data={'title': 'Dark mode'})
    people = {'pat@example.org': {'events'}, 'vic@example.org': {'invoices'}}
    auth.lookup = (lambda old: lambda e: dict(old(e), perms=people[e]) if e in people else old(e))(auth.lookup)
    pat, vic = as_('pat@example.org'), as_('vic@example.org')
    pat.post('/hassles/new', data={'title': 'Another'})
    pat.post('/hassles/1/reply', data={'text': 'hi'})
    assert len(S.hassles()) == 1 and S.get(1)['replies'] == []          # an official without it can look, not post
    page = pat.get('/hassles/').get_data(as_text=True)
    assert 'Dark mode' in page and 'Post it' not in page and 'not part of your role' in page
    assert vic.get('/hassles/').status_code == 302                      # a volunteer without it can't open it
    pat.post('/hassles/1/delete')
    assert S.hassles() == []                                             # your own post can always be deleted
