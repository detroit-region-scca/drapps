"""The shared local copy of the sheets: served from SQLite, refetched after invalidate, refusals remembered."""
import gspread
import pytest


class FakeBook:
    def __init__(self):
        self.calls = 0
        self.value = 'A'
        self.fail = None

    def values_batch_get(self, ranges, params=None):
        self.calls += 1
        if self.fail:
            raise self.fail
        return {'valueRanges': [{'values': [[self.value]]}]}


def _err(code):
    class R:
        text = 'x'

        def json(self):
            return {'error': {'code': code, 'message': 'x', 'status': 'X'}}
    return gspread.exceptions.APIError(R())


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv('STORAGE_BACKEND', 'sheets')
    monkeypatch.setenv('SHEET_CACHE', '1')
    import sheet_cache as C
    monkeypatch.setattr(C, 'DB', str(tmp_path / 'c.sqlite'))
    monkeypatch.setattr(C, 'POLL_SECONDS', 0)
    monkeypatch.setattr(C, '_local', type(C._local)())
    book = FakeBook()
    monkeypatch.setattr(C, 'open_book', lambda sid: book)
    C.book = book
    return C


def test_second_read_comes_from_the_copy(cache):
    assert cache.batch_get('s1', ['A:A'])['valueRanges'][0]['values'] == [['A']]
    cache.book.value = 'B'
    assert cache.batch_get('s1', ['A:A'])['valueRanges'][0]['values'] == [['A']]
    assert cache.book.calls == 1


def test_invalidate_makes_the_next_read_fresh_and_changes_the_version(cache):
    cache.batch_get('s1', ['A:A'])
    v = cache.version('s1')
    cache.book.value = 'B'
    cache.invalidate('s1')
    assert cache.version('s1') != v
    assert cache.batch_get('s1', ['A:A'])['valueRanges'][0]['values'] == [['B']]
    assert cache.book.calls == 2


def test_other_sheets_are_untouched_by_invalidate(cache):
    cache.batch_get('s1', ['A:A'])
    cache.batch_get('s2', ['A:A'])
    cache.invalidate('s1')
    cache.batch_get('s2', ['A:A'])
    assert cache.book.calls == 2


def test_a_refusal_is_remembered_and_raised_as_api_error(cache):
    cache.book.fail = _err(400)
    for _ in range(3):
        with pytest.raises(gspread.exceptions.APIError):
            cache.batch_get('s1', ['Missing!A:A'])
    assert cache.book.calls == 1


def test_quota_errors_are_not_remembered(cache):
    cache.book.fail = _err(429)
    with pytest.raises(gspread.exceptions.APIError):
        cache.batch_get('s1', ['A:A'])
    cache.book.fail = None
    assert cache.batch_get('s1', ['A:A'])['valueRanges'][0]['values'] == [['A']]


def test_poll_refetches_only_changed_sheets(cache, monkeypatch):
    cache.batch_get('s1', ['A:A'])
    times = {'s1': 't1'}
    monkeypatch.setattr(cache, '_modified', lambda sid: times[sid])
    cache.poll_once()                       # first look records the time (and refreshes once)
    calls = cache.book.calls
    cache.poll_once()                       # unchanged: nothing fetched
    assert cache.book.calls == calls
    times['s1'] = 't2'
    cache.book.value = 'C'
    cache.poll_once()
    assert cache.book.calls == calls + 1
    assert cache.batch_get('s1', ['A:A'])['valueRanges'][0]['values'] == [['C']]


def test_off_switch_reads_google_every_time(cache, monkeypatch):
    monkeypatch.setenv('SHEET_CACHE', '0')
    cache.batch_get('s1', ['A:A'])
    cache.batch_get('s1', ['A:A'])
    assert cache.book.calls == 2
