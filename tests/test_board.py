import motions_store as M
from board import _how_of, _vote_of


def test_next_motion_id():
    assert M._next_id([], 2026) == 'M-2026-001'
    assert M._next_id(['M-2026-001', 'M-2026-009', 'M-2025-020'], 2026) == 'M-2026-010'


def test_how_and_vote_read_back_from_approved_by():
    assert _how_of('Nathan Crosty (email vote, 7-0)') == 'email vote'
    assert _vote_of('Nathan Crosty (email vote, 7-0)') == '7-0'
    assert _how_of('Nathan Crosty (board meeting)') == 'board meeting' and _vote_of('Nathan Crosty (board meeting)') == ''
    assert _how_of('Nathan Crosty') == '' and _vote_of('Nathan Crosty') == ''


def test_summary():
    assert M.summary({'title': 'Set aside $80,000', 'amount': 80000.0}) == 'Set aside $80,000'
