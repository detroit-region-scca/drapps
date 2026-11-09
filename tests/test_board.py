import motions_store as M
from board import _how_of, _vote_of


def test_next_motion_id():
    assert M._next_id([], 2026) == 'M-2026-001'
    assert M._next_id(['M-2026-001', 'M-2026-009', 'M-2025-020'], 2026) == 'M-2026-010'


def test_how_and_vote_read_back_from_approved_by():
    assert _how_of('Pat Example (email vote, 7-0)') == 'email vote'
    assert _vote_of('Pat Example (email vote, 7-0)') == '7-0'
    assert _how_of('Pat Example (board meeting)') == 'board meeting' and _vote_of('Pat Example (board meeting)') == ''
    assert _how_of('Pat Example') == '' and _vote_of('Pat Example') == ''


def test_summary():
    assert M.summary({'title': 'Set aside $80,000', 'amount': 80000.0}) == 'Set aside $80,000'


def test_request_email_vote_can_be_marked_urgent(monkeypatch):
    import app as A
    import auth
    import board
    sent = []
    motion = {'id': 'M-2026-009', 'title': 'Buy a trailer', 'status': 'open', 'details': '', 'link': '', 'proposed_by': 'Someone'}
    monkeypatch.setattr(auth, 'lookup', lambda e: {'email': e, 'name': 'Someone', 'level': 'officer', 'roles': ['Treasurer'],
                                                   'perms': {'board_ask'}})
    monkeypatch.setattr(board, '_me', lambda: {'level': 'officer', 'perms': {'board_ask'}})
    monkeypatch.setattr(board.M, 'enabled', lambda: True)
    monkeypatch.setattr(board.B, 'enabled', lambda: False)   # no live budgets sheet in tests
    monkeypatch.setattr(board.BS, 'enabled', lambda: False)  # no ballots sheet: the old reply-all request
    monkeypatch.setattr(board.M, 'motions', lambda: [motion])
    monkeypatch.setattr(board.M, 'update', lambda *a, **k: None)
    monkeypatch.setattr(board, 'send_mail', lambda subject, body, *a, **k: sent.append((subject, body)))
    monkeypatch.setattr(board, 'log_audit_event', lambda *a, **k: None)
    c = A.app.test_client()
    with c.session_transaction() as s:
        s['authenticated'] = True
        s['email'] = 'someone@example.org'
    page = c.get('/board/').get_data(as_text=True)
    assert 'Request email vote' in page and 'Ask the board' not in page
    c.post('/board/', data={'action': 'ask', 'item': 'motion:M-2026-009', 'urgent': 'yes'})
    c.post('/board/', data={'action': 'ask', 'item': 'motion:M-2026-009'})
    assert sent[0][0] == 'URGENT Board vote: Buy a trailer' and sent[0][1].startswith('URGENT: please vote')
    assert sent[1][0] == 'Board vote: Buy a trailer' and 'URGENT' not in sent[1][1]
