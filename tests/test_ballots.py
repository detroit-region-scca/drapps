"""Board email votes: the roster and counting rules (bylaws Art. IV, the 2018 online-voting policy) and the flow
through the Board app with an in-memory store. All people here are made up."""
import re

import pytest

import ballots_logic as BL

OFFICIALS = [
    {'role': 'RE', 'title': 'Region Executive', 'name': 'Pat One', 'email': 'one@example.org'},
    {'role': 'ARE', 'title': 'Assistant Region Executive', 'name': 'Sam Two', 'email': 'two@example.org'},
    {'role': 'Secretary', 'title': 'Secretary', 'name': 'Lee Three', 'email': 'three@example.org'},
    {'role': 'Treasurer', 'title': 'Treasurer', 'name': 'Kim Four', 'email': 'four@example.org'},
    {'role': 'Financial Director', 'title': 'Financial Director', 'name': 'Ash Five', 'email': 'five@example.org'},
    {'role': 'Director', 'program': 'RR', 'title': 'Road Racing Director', 'name': 'Lee Three', 'email': 'THREE@example.org'},
    {'role': 'Director', 'program': 'AX', 'title': 'Autocross Director', 'name': 'Jo Six', 'email': 'six@example.org'},
    {'role': 'Director', 'program': 'RY', 'title': 'Road Rally Director', 'name': 'Max Seven', 'email': 'seven@example.org'},
    {'role': 'Director', 'program': 'RX', 'title': 'RallyCross Director', 'name': 'Ray Eight', 'email': 'eight@example.org'},
    {'role': 'Director', 'program': 'SIM', 'title': 'SIM Racing', 'name': 'Not Aseat', 'email': 'sim@example.org'},
    {'role': 'Director', 'program': 'DR', 'title': 'General Events approver (Region Executive)', 'name': 'Pat One', 'email': 'one@example.org'},
    {'role': 'Director at Large', 'title': 'Director at Large', 'name': 'Dee Nine', 'email': 'nine@example.org'},
    {'role': 'Director at Large', 'title': 'Director at Large', 'name': 'Bo Ten', 'email': 'ten@example.org'},
    {'role': 'Director at Large', 'title': 'Director at Large', 'name': 'Cy Eleven', 'email': 'eleven@example.org'},
    {'role': 'Director at Large', 'title': 'Director at Large', 'name': 'No Email', 'email': ''},
    {'role': 'Equipment', 'title': 'Equipment Committee', 'name': 'Not Aseat Either', 'email': 'eq@example.org'},
]


def test_roster_is_board_seats_one_vote_per_person():
    r = BL.roster(OFFICIALS)
    emails = [p['email'] for p in r]
    assert len(r) == 11 and len(set(emails)) == 11                         # Lee Three holds two seats, votes once
    assert next(p for p in r if p['name'] == 'Lee Three')['seats'] == ['Secretary', 'Road Racing Director']
    assert 'sim@example.org' not in emails and 'eq@example.org' not in emails  # not board seats
    assert all(p['email'] for p in r)                                       # a seat with no address is skipped


def _v(email, choice, at):
    return {'email': email, 'choice': choice, 'at': at}


def test_tally_counts_each_persons_last_vote_and_ignores_non_voters():
    people = BL.roster(OFFICIALS)
    t = BL.tally([_v('one@example.org', 'no', '2026-11-01 10:00'), _v('ONE@example.org', 'yes', '2026-11-02 10:00'),
                  _v('two@example.org', 'abstain', '2026-11-01 11:00'), _v('stranger@example.org', 'yes', '2026-11-01 12:00')], people)
    assert (t['yes'], t['no'], t['abstain'], t['voted'], t['of']) == (1, 0, 1, 2, 11)
    row = {r['name']: r for r in t['rows']}
    assert row['Pat One']['choice'] == 'yes' and row['Jo Six']['choice'] == ''


def test_seven_decide_it_and_thirty_days_close_it():
    people = BL.roster(OFFICIALS)
    yes6 = [_v(p['email'], 'yes', f'2026-11-0{i} 09:00') for i, p in enumerate(people[:6], 1)]
    t = BL.tally(yes6, people)
    b = {'status': 'open', 'opened': '2026-11-01', 'closes': BL.closes('2026-11-01')}
    assert b['closes'] == '2026-12-01'
    assert BL.outcome(t) is None and BL.state(b, t, '2026-11-20') == 'open'
    assert BL.state(b, t, '2026-12-02') == 'expired'
    t7 = BL.tally(yes6 + [_v(people[6]['email'], 'yes', '2026-11-07 09:00')], people)
    assert BL.outcome(t7) == 'passed' and BL.state(b, t7, '2026-12-02') == 'passed'
    no7 = BL.tally([_v(p['email'], 'no', '2026-11-01 09:00') for p in people[:7]], people)
    assert BL.outcome(no7) == 'failed'
    mixed = BL.tally(yes6 + [_v(p['email'], 'abstain', '2026-11-01 09:00') for p in people[6:9]], people)
    assert BL.outcome(mixed) is None and BL.count_text(mixed) == '6-0, 3 abstained'


def test_summary_lines():
    people = BL.roster(OFFICIALS)
    t = BL.tally([_v('one@example.org', 'yes', 'x'), _v('two@example.org', 'no', 'y')], people)
    b = {'status': 'open', 'closes': '2026-12-01'}
    assert BL.summary(b, t, '2026-11-05') == '2 of 11 voted: 1 yes, 1 no. 7 decide it. Closes 2026-12-01.'
    assert BL.summary({'status': 'recorded', 'result': 'passed'}, t) == 'Passed 1-1 by email vote.'


# ------------------------------------------------------------------ the flow through the Board app

class FakeStore:
    def __init__(self):
        self.rows, self.vs, self.n = [], [], 0

    def enabled(self):
        return True

    def ballots(self):
        return [dict(b) for b in self.rows]

    def votes(self, bid=None):
        return [dict(v) for v in self.vs if bid is None or v['ballot_id'] == bid]

    def get(self, bid):
        return next((dict(b) for b in self.rows if b['id'] == bid), None)

    def current(self, item):
        bs = [b for b in self.rows if b['item'] == item and b['status'] != 'cancelled']
        return dict(bs[-1]) if bs else None

    def open_ballot(self, item, title, who, today):
        for b in self.rows:
            if b['item'] == item and b['status'] in ('open', 'expired'):
                b['status'] = 'cancelled'
        self.n += 1
        bid = f'B-2026-{self.n:03d}'
        self.rows.append({'id': bid, 'item': item, 'title': title, 'opened': today, 'opened_by': who,
                          'closes': BL.closes(today), 'status': 'open', 'result': '', 'count': '', 'decided': '', 'recorded_by': ''})
        return bid

    def update(self, bid, **f):
        next(b for b in self.rows if b['id'] == bid).update(f)

    def cast(self, bid, email, name, choice, via):
        self.vs.append({'ballot_id': bid, 'email': email, 'name': name, 'choice': choice, 'at': f'2026-11-05 10:{len(self.vs):02d}', 'via': via})


@pytest.fixture()
def env(monkeypatch):
    import app as A
    import auth
    import board
    store, sent, updates = FakeStore(), [], []
    motion = {'id': 'M-2026-009', 'title': 'Buy a trailer', 'status': 'open', 'details': '', 'link': '', 'proposed_by': 'Pat One',
              'proposed': '2026-11-01', 'asked': ''}
    monkeypatch.setattr(board, 'BS', store)
    monkeypatch.setattr(board.O, 'all', lambda: [dict(o) for o in OFFICIALS])
    monkeypatch.setattr(board.M, 'enabled', lambda: True)
    monkeypatch.setattr(board.B, 'enabled', lambda: False)
    monkeypatch.setattr(board.M, 'motions', lambda: [dict(motion)])
    monkeypatch.setattr(board.M, 'get', lambda mid: dict(motion) if mid == motion['id'] else None)
    monkeypatch.setattr(board.M, 'update', lambda mid, **k: updates.append((mid, k)))
    monkeypatch.setattr(board, 'send_mail', lambda subject, body, to=None, *a, **k: sent.append((subject, body, to)))
    monkeypatch.setattr(board, 'log_audit_event', lambda *a, **k: None)
    import fr_store
    monkeypatch.setattr(fr_store, 'requests', lambda: [])     # no funds requests sheet in tests
    me = {'email': 'four@example.org'}
    monkeypatch.setattr(auth, 'lookup', lambda e: e and {'email': e, 'name': 'Kim Four', 'level': 'officer', 'roles': ['Treasurer'], 'perms': {'board_ask'}})
    monkeypatch.setattr(board, '_me', lambda: {'level': 'officer', 'perms': {'board_ask'}})
    c = A.app.test_client()
    with c.session_transaction() as s:
        s['authenticated'] = True
        s['email'] = me['email']
        s['who'] = 'Kim Four'
    return dict(c=c, store=store, sent=sent, updates=updates, board=board, monkeypatch=monkeypatch)


def _links(body):
    """The personal vote links in an email: short links, resolved to the signed link each one stands for."""
    import shortlinks
    short = re.findall(r'https?://[^\s]+/l/([^\s]+)', body)
    return [u for u in (shortlinks._load()[sid]['url'] for sid in short) if '/board/vote/' in u]


def test_request_email_vote_sends_board_thread_and_a_personal_link_each(env):
    c, sent, store = env['c'], env['sent'], env['store']
    c.post('/board/', data={'action': 'ask', 'item': 'motion:M-2026-009'})
    assert len(store.rows) == 1 and store.rows[0]['item'] == 'motion:M-2026-009'
    thread = [s for s in sent if s[0] == 'Discussion: Buy a trailer']
    personal = [s for s in sent if s[0].startswith('Vote: ')]
    assert len(thread) == 1 and 'reply all with your vote' not in thread[0][1] and 'Discuss here (reply all)' in thread[0][1]
    assert sorted(s[2] for s in personal) == sorted(p['email'] for p in BL.roster(OFFICIALS))
    assert all(len(_links(s[1])) == 1 for s in personal)


def test_vote_by_link_roll_call_decision_and_recording(env):
    c, sent, store, board = env['c'], env['sent'], env['store'], env['board']
    c.post('/board/', data={'action': 'ask', 'item': 'motion:M-2026-009'})
    links = {s[2]: _links(s[1])[0] for s in sent if s[0].startswith('Vote: ')}
    page = c.get('/board/').get_data(as_text=True)
    assert 'Roll call' in page and page.count('Not yet') == 11 and '0 of 11 voted' in page
    path = lambda u: '/' + u.split('://', 1)[1].split('/', 1)[1]

    def vote(e, choice):      # each director from their own browser, opening the emailed link first
        b = env['c'].application.test_client()
        b.get(path(links[e]))
        return b.post(path(links[e]), data={'choice': choice})
    g = env['c'].application.test_client().get(path(links['six@example.org'])).get_data(as_text=True)
    assert 'Voting as <strong>Jo Six</strong>' in g
    voters = [p['email'] for p in BL.roster(OFFICIALS)]
    for e in voters[:6]:
        vote(e, 'yes')
    assert store.rows[0]['status'] == 'open'
    vote(voters[6], 'no')
    vote(voters[6], 'yes')       # changed their mind: the last vote counts
    assert store.rows[0]['status'] == 'passed' and store.rows[0]['count'] == '7-0'
    assert any(s[0] == 'Board vote passed: Motion M-2026-009: Buy a trailer' for s in sent)
    late = vote(voters[7], 'no').get_data(as_text=True)
    assert 'already closed' in late
    page = c.get('/board/').get_data(as_text=True)
    assert 'Passed 7-0 by email vote' in page
    env['monkeypatch'].setattr(board, 'officer_current', lambda *a: {'name': 'Kim Four'})
    c.post(f"/board/ballot/{store.rows[0]['id']}/record")
    mid, k = env['updates'][-1]
    assert mid == 'M-2026-009' and k['status'] == 'passed' and k['how'] == 'email vote' and k['vote'] == '7-0'
    assert store.rows[0]['status'] == 'recorded' and store.rows[0]['recorded_by'] == 'Kim Four'


def test_board_page_vote_and_remind_only_the_ones_not_voted(env):
    c, sent, store = env['c'], env['sent'], env['store']
    c.post('/board/', data={'action': 'ask', 'item': 'motion:M-2026-009'})
    bid = store.rows[0]['id']
    c.post(f'/board/ballot/{bid}/vote', data={'choice': 'abstain'})          # signed in as a director (Kim Four)
    assert store.vs[-1]['email'] == 'four@example.org' and store.vs[-1]['via'] == 'board page'
    sent.clear()
    c.post(f'/board/ballot/{bid}/remind')
    assert len(sent) == 10 and 'four@example.org' not in [s[2] for s in sent]


def test_a_non_director_cannot_vote_from_the_board_page(env):
    c, store = env['c'], env['store']
    c.post('/board/', data={'action': 'ask', 'item': 'motion:M-2026-009'})
    with c.session_transaction() as s:
        s['email'] = 'sim@example.org'
    c.post(f"/board/ballot/{store.rows[0]['id']}/vote", data={'choice': 'yes'})
    assert store.vs == []


def test_a_forged_vote_link_is_refused(env):
    anon = env['c'].application.test_client()
    assert 'expired or is not valid' in anon.get('/board/vote/not-a-real-token').get_data(as_text=True)
