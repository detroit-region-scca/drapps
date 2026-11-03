from bud import _how_vote


def test_how_and_vote_text():
    assert _how_vote({'how': 'email', 'vote': '7-0'}) == 'email vote, 7-0'
    assert _how_vote({'how': 'meeting', 'vote': ''}) == 'board meeting'
    assert _how_vote({'how': 'meeting', 'vote': '6 - 1<script>'}) == 'board meeting, 6 - 1'
    assert _how_vote({}) == ''
