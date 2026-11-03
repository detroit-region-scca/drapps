from bud import _how_vote


def test_how_and_vote_text():
    assert _how_vote({'how': 'email', 'vote': '7-0'}) == 'email vote, 7-0'
    assert _how_vote({'how': 'meeting', 'vote': ''}) == 'board meeting'
    assert _how_vote({'how': 'meeting', 'vote': '6 - 1<script>'}) == 'board meeting, 6 - 1'
    assert _how_vote({}) == ''


def test_sheet_day_number_dates_read_back_as_iso():
    import bud_store
    assert bud_store._read_cell('date', 46277) == '2026-09-12'
    assert bud_store._read_cell('date', '2026-09-12') == '2026-09-12'
    assert bud_store._read_cell('event_name', 46277) == '46277'
