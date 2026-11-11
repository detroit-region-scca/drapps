"""Hassles: anyone signed in posts and gives thumbs up; not on their own; the admin sets the status."""
import pytest


@pytest.fixture()
def box(monkeypatch, tmp_path):
    import auth
    import hassles as suggestions
    people = {'pat@example.org': {'name': 'Pat', 'level': 'official', 'roles': ['Director'], 'perms': {'events'}},
              'vic@example.org': {'name': 'Vic', 'level': 'volunteer', 'roles': [], 'perms': {'invoices'}},
              'ada@example.org': {'name': 'Ada', 'level': 'officer', 'roles': ['Treasurer'], 'perms': {'admin'}}}
    monkeypatch.setattr(auth, 'lookup', lambda e: dict(people[e], email=e) if e in people else None)
    monkeypatch.setattr(suggestions, 'PATH', str(tmp_path / 'suggestions.json'))
    monkeypatch.setattr(suggestions, 'log_audit_event', lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(suggestions, 'send_mail', lambda subject, body, to, **k: sent.append((subject, body, to)))
    from app import app
    app.config['TESTING'] = True

    def as_(email):
        c = app.test_client()
        with c.session_transaction() as s:
            s.update(authenticated=True, email=email, who=people[email]['name'])
        return c
    as_.sent = sent
    return as_, suggestions


def test_post_vote_and_status(box):
    as_, S = box
    pat, vic, ada = as_('pat@example.org'), as_('vic@example.org'), as_('ada@example.org')
    pat.post('/hassles/new', data={'title': 'Dark mode', 'details': 'Easier at night'})
    vic.post('/hassles/new', data={'title': 'Event map'})
    s1, s2 = S._load()['items']
    assert as_.sent[0][0] == 'New hassle: Dark mode' and 'Pat posted' in as_.sent[0][1] and as_.sent[0][2] == 'admin@drscca.org'
    pat.post(f"/hassles/{s1['id']}/vote")                     # not on your own
    vic.post(f"/hassles/{s1['id']}/vote")                     # a volunteer can vote
    ada.post(f"/hassles/{s1['id']}/vote")
    ada.post(f"/hassles/{s1['id']}/vote")                     # pressed again: taken back
    assert S._load()['items'][0]['votes'] == ['vic@example.org']
    page = vic.get('/hassles/').get_data(as_text=True)
    assert page.index('Dark mode') < page.index('Event map') and 'Pat' in page
    pat.post(f"/hassles/{s1['id']}/status", data={'status': 'Fixed', 'note': 'x'})
    assert S._load()['items'][0]['status'] == 'Open'               # only the admin
    ada.post(f"/hassles/{s1['id']}/status", data={'status': 'Planned', 'note': 'Next month'})
    assert S._load()['items'][0]['status'] == 'Planned' and 'Next month' in vic.get('/hassles/').get_data(as_text=True)


def test_delete_is_for_the_poster_or_the_admin(box):
    as_, S = box
    as_('pat@example.org').post('/hassles/new', data={'title': 'Dark mode'})
    sid = S._load()['items'][0]['id']
    as_('vic@example.org').post(f'/hassles/{sid}/delete')
    assert len(S._load()['items']) == 1
    as_('pat@example.org').post(f'/hassles/{sid}/delete')
    assert S._load()['items'] == []
    as_('pat@example.org').post('/hassles/new', data={'title': ''})
    assert S._load()['items'] == []                               # a title is required
