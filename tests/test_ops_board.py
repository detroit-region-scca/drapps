"""Board: a decision is recorded once, a budget changed during its email vote is not approved by that vote, the
"passed" email goes out once, and the Decisions page shows a meeting's minutes. Made-up names only."""
import pytest

import ballots_logic as BL
from test_ballots import OFFICIALS, FakeStore


@pytest.fixture()
def env(monkeypatch, tmp_path):
    import app as A
    import auth
    import board
    import fr_store
    store, sent, recorded = FakeStore(), [], []
    motions = {'M-2026-009': {'id': 'M-2026-009', 'title': 'Buy a trailer', 'status': 'open', 'details': '', 'link': '', 'proposed_by': 'Pat One',
                              'proposed': '2026-11-01', 'asked': '', 'decided': '', 'how': '', 'vote': ''}}
    monkeypatch.setattr(board, 'BS', store)
    monkeypatch.setattr(board, 'RECORD_LOCK', str(tmp_path / 'record.lock'))
    monkeypatch.setattr(board, 'VOTE_LOCK', str(tmp_path / 'vote.lock'))
    monkeypatch.setattr(board.O, 'all', lambda: [dict(o) for o in OFFICIALS])
    monkeypatch.setattr(board.M, 'enabled', lambda: True)
    monkeypatch.setattr(board.B, 'enabled', lambda: False)
    monkeypatch.setattr(board.P, 'enabled', lambda: False)
    monkeypatch.setattr(board.M, 'motions', lambda: [dict(m) for m in motions.values()])
    monkeypatch.setattr(board.M, 'get', lambda mid: dict(motions[mid]) if mid in motions else None)

    def update(mid, **k):
        motions[mid].update(k)
        recorded.append((mid, k))
    monkeypatch.setattr(board.M, 'update', update)
    monkeypatch.setattr(board, 'send_mail', lambda subject, body, to=None, *a, **k: sent.append((subject, body, to)))
    monkeypatch.setattr(board, 'log_audit_event', lambda *a, **k: None)
    monkeypatch.setattr(fr_store, 'requests', lambda: [])
    me = {'email': 'four@example.org', 'name': 'Kim Four', 'level': 'officer', 'roles': ['Treasurer'], 'perms': {'board_ask', 'board', 'audit'}}
    monkeypatch.setattr(auth, 'lookup', lambda e: dict(me, email=e) if e else None)
    monkeypatch.setattr(board, 'officer_current', lambda *a: {'name': 'Kim Four', 'email': 'four@example.org'})
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update(authenticated=True, email=me['email'], who='Kim Four')
    return dict(c=c, app=A.app, store=store, sent=sent, recorded=recorded, motions=motions, board=board, monkeypatch=monkeypatch)


def _flashes(c):
    with c.session_transaction() as s:
        return ' | '.join(m for _, m in s.pop('_flashes', []))


def _decided(store, item, title, snap=''):
    """A ballot on this item that 7 directors voted yes on (decided, not recorded yet)."""
    bid = store.open_ballot(item, title, 'Kim Four', '2026-11-01', snap=snap)
    for p in BL.roster(OFFICIALS)[:7]:
        store.cast(bid, p['email'], p['name'], 'yes', 'email link')
    return bid


def test_recording_from_the_waiting_list_twice_records_once(env):
    c = env['c']
    form = {'item': 'motion:M-2026-009', 'decided': '2026-11-05', 'result': 'passed', 'how': 'meeting', 'vote': '8-0'}
    c.post('/board/', data=form)
    assert len(env['recorded']) == 1 and env['motions']['M-2026-009']['status'] == 'passed'
    assert 'Recorded for 2026-11-05' in _flashes(c)
    c.post('/board/', data=form)                         # the double click, or a second officer
    assert len(env['recorded']) == 1


def test_a_budget_approved_meanwhile_is_not_recorded_again(env):
    import bud
    c, board, calls = env['c'], env['board'], []
    env['monkeypatch'].setattr(board.B, 'enabled', lambda: True)
    waiting = [{'b': {'event_code': '#EV1'}}]
    env['monkeypatch'].setattr(bud, '_pending_rows', lambda y, only='': list(waiting))
    env['monkeypatch'].setattr(bud, 'record_budgets', lambda y, codes, approved, how: calls.append((y, list(codes))) or len(codes))
    form = {'item': 'budget:2027:#EV1', 'decided': '2026-11-05', 'result': 'passed', 'how': 'meeting'}
    c.post('/board/', data=form)
    assert calls == [('2027', ['#EV1'])]
    waiting.clear()                                      # it is approved now
    c.post('/board/', data=form)
    assert len(calls) == 1 and 'already recorded' in _flashes(c)


def test_email_vote_is_recorded_once(env):
    c, store = env['c'], env['store']
    bid = _decided(store, 'motion:M-2026-009', 'Motion M-2026-009: Buy a trailer')
    c.post(f'/board/ballot/{bid}/record')
    c.post(f'/board/ballot/{bid}/record')
    assert len(env['recorded']) == 1 and store.rows[0]['status'] == 'recorded'
    assert 'already recorded' in _flashes(c)


def test_a_budget_changed_during_the_vote_is_not_approved_by_it(env):
    import bud
    c, store, board, calls = env['c'], env['store'], env['board'], []
    env['monkeypatch'].setattr(board.B, 'enabled', lambda: True)
    env['monkeypatch'].setattr(bud, '_pending_rows', lambda y, only='': [{'b': {'event_code': '#EV1'}}, {'b': {'event_code': '#EV2'}}])
    env['monkeypatch'].setattr(bud, 'record_budgets', lambda y, codes, approved, how: calls.append(list(codes)) or len(codes))
    lines = {'#EV1': [{'section': 'Expense', 'description': 'Site rental', 'budget': 500.0}],
             '#EV2': [{'section': 'Expense', 'description': 'Trophies', 'budget': 100.0}]}
    env['monkeypatch'].setattr(board.B, 'get', lambda code: {'lines': [dict(ln) for ln in lines[code]]} if code in lines else None)
    with env['app'].test_request_context():
        snap = board._snap('budget:2027:#EV1')
        assert snap and snap == board._snap('budget:2027:#EV1') and board._snap('motion:M-2026-009') == ''
    bid = _decided(store, 'budget:2027:#EV1', 'Budget: Test Solo', snap=snap)
    old = _decided(store, 'budget:2027:#EV2', 'Budget: Old Vote')          # opened before fingerprints were kept
    lines['#EV1'][0]['budget'] = 900.0                                      # raised while the board was voting
    c.post(f'/board/ballot/{bid}/record')
    assert calls == [] and 'changed after this vote opened' in _flashes(c)
    assert store.get(bid)['status'] == 'open'
    lines['#EV1'][0]['budget'] = 500.0                                      # put back as voted on: it can be recorded
    c.post(f'/board/ballot/{bid}/record')
    c.post(f'/board/ballot/{old}/record')
    assert calls == [['#EV1'], ['#EV2']]


def test_two_last_votes_send_one_decided_email(env):
    store, board = env['store'], env['board']
    bid = _decided(store, 'motion:M-2026-009', 'Motion M-2026-009: Buy a trailer')
    with env['app'].test_request_context():
        assert board._decide_if_done(bid) == 'passed'
        assert board._decide_if_done(bid) is None          # the other worker, a moment later
    assert len([s for s in env['sent'] if s[0].startswith('Board vote passed')]) == 1


def test_decisions_show_the_months_minutes_on_the_meeting_date(env):
    import minutes_store
    board, m = env['board'], env['motions']
    m['M-2026-009'].update(status='passed', decided='2026-11-12', how='board meeting', vote='8-0')
    m['M-2026-010'] = dict(m['M-2026-009'], id='M-2026-010', title='Renew the lease', decided='2026-11-20', how='email vote', vote='7-0')
    files = [{'name': '2026-11 Board Minutes (special meeting).pdf', 'webViewLink': 'special'},
             {'name': '2026-11 Board Minutes.pdf', 'webViewLink': 'regular'},
             {'name': '2026 Budgets approved by email.pdf', 'webViewLink': 'budgets'}]
    env['monkeypatch'].setattr(minutes_store, 'enabled', lambda: True)
    env['monkeypatch'].setattr(minutes_store, 'published', lambda year: files)
    with env['app'].test_request_context():
        days = {d['date']: d for d in board.decisions('2026')}
    assert days['2026-11-12']['minutes']['webViewLink'] == 'regular'      # minutes are filed by month, the meeting was on the 12th
    assert days['2026-11-20']['minutes'] is None                          # an email vote is not a meeting


def test_decisions_page_survives_a_bad_year_in_the_address(env):
    assert env['c'].get('/board/decisions?year=abc').status_code == 200
    assert env['c'].get('/board/decisions?year=20266').status_code == 200


def test_the_vote_page_keeps_the_signed_in_persons_menu(env):
    store, board = env['store'], env['board']
    bid = store.open_ballot('motion:M-2026-009', 'Motion M-2026-009: Buy a trailer', 'Kim Four', '2026-11-01')
    with env['app'].test_request_context():
        token = board._ser().dumps({'b': bid, 'e': 'six@example.org', 'n': 'Jo Six'})
    page = env['c'].get(f'/board/vote/{token}').get_data(as_text=True)
    assert 'Voting as <strong>Jo Six</strong>' in page and 'Audit log' in page
