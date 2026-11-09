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
    monkeypatch.setattr(cache, '_modified', lambda sid: {'modifiedTime': times[sid]})
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


def test_remembered_refusal_is_retried_after_a_while(cache, monkeypatch):
    cache.book.fail = _err(400)
    with pytest.raises(gspread.exceptions.APIError):
        cache.batch_get('s1', ['Missing!A:A'])
    cache.book.fail = None
    with pytest.raises(gspread.exceptions.APIError):     # still remembered
        cache.batch_get('s1', ['Missing!A:A'])
    assert cache.book.calls == 1
    real = cache.time.time
    monkeypatch.setattr(cache.time, 'time', lambda: real() + cache.ERROR_SECONDS + 1)
    assert cache.batch_get('s1', ['Missing!A:A'])['valueRanges'][0]['values'] == [['A']]


def test_a_deleted_sheet_is_dropped_from_the_poll(cache, monkeypatch):
    cache.batch_get('gone', ['A:A'])
    def missing(sid):
        raise cache.SheetGone(sid)
    monkeypatch.setattr(cache, '_modified', missing)
    cache.poll_once()
    assert cache._db().execute("SELECT COUNT(*) FROM snaps WHERE sheet_id = 'gone'").fetchone()[0] == 0


def test_hand_edit_is_logged_app_edit_is_not(cache, monkeypatch):
    import utils
    logged = []
    monkeypatch.setattr(cache, '_hand_logged', {})
    monkeypatch.setattr(utils, 'log_audit_event', lambda a, u, d: logged.append((a, u, d)))
    cache.batch_get('s1', ['A:A'])
    info = {'modifiedTime': 't1', 'name': 'Ops', 'lastModifyingUser': {'displayName': 'Pat', 'emailAddress': 'pat@x.com'}}
    monkeypatch.setattr(cache, '_modified', lambda sid: info)
    cache.poll_once()                       # first look: no log
    info.update(modifiedTime='t2', lastModifyingUser={'emailAddress': 'app@proj.iam.gserviceaccount.com'})
    cache.poll_once()                       # the apps' own write: no log
    info.update(modifiedTime='t3', lastModifyingUser={'displayName': 'Pat', 'emailAddress': 'pat@x.com'})
    cache.poll_once()
    info.update(modifiedTime='t4')
    cache.poll_once()                       # Pat still editing: not logged again
    assert [(a, u, d['sheet']) for a, u, d in logged] == [('SHEET_EDITED', 'Pat', 'Ops')]


def test_a_write_during_a_slow_read_is_not_lost(cache):
    """The read started before the write, so its answer may be the old one: it must not be kept as current."""
    cache.batch_get('s1', ['A:A'])
    cache.invalidate('s1')
    slow = cache.book.values_batch_get

    def read_then_write_lands(ranges, params=None):
        out = slow(ranges, params)            # Google answers with the old value...
        cache.book.value = 'B'
        cache.invalidate('s1')                # ...and another worker's write finishes before it is stored
        return out
    cache.book.values_batch_get = read_then_write_lands
    assert cache.batch_get('s1', ['A:A'])['valueRanges'][0]['values'] == [['A']]
    cache.book.values_batch_get = slow
    assert cache.batch_get('s1', ['A:A'])['valueRanges'][0]['values'] == [['B']]


def test_an_old_copy_gains_the_writes_column(cache):
    import sqlite3
    c = sqlite3.connect(cache.DB)
    c.execute('CREATE TABLE sheets (sheet_id TEXT PRIMARY KEY, version INTEGER DEFAULT 0, modified TEXT DEFAULT "", checked REAL DEFAULT 0)')
    c.commit()
    c.close()
    cache.batch_get('s1', ['A:A'])
    cache.invalidate('s1')
    assert cache._writes(cache._db(), 's1') == 1


def test_the_poller_outlives_a_lock_it_cannot_take(cache, monkeypatch):
    """A lock file owned by root used to end the poller thread for good."""
    tries, polls = [], []

    class Lock:
        def __init__(self, path):
            pass

        def acquire(self, timeout=0):
            tries.append(1)
            if len(tries) < 3:
                raise PermissionError('lock file is owned by root')

        def release(self):
            raise OSError('cannot release either')

    class Stop(BaseException):
        pass

    def poll():
        polls.append(1)
        raise Stop()
    monkeypatch.setattr(cache, 'FileLock', Lock)
    monkeypatch.setattr(cache, 'poll_once', poll)
    monkeypatch.setattr(cache.time, 'sleep', lambda s: None)
    with pytest.raises(Stop):
        cache._poll_forever()
    assert len(tries) == 3 and polls == [1]


def test_store_cache_reloads_after_a_write_that_lands_mid_load(cache, monkeypatch):
    import storage
    st = storage.SheetStore('s1')
    cache.batch_get('s1', ['A:A'])
    seen = []

    def load():
        seen.append(1)
        if len(seen) == 1:
            cache.invalidate('s1')            # a write lands while the first load is running
        return len(seen)
    assert st._cached('k', load) == 1
    assert st._cached('k', load) == 2         # not the answer from before the write
    assert st._cached('k', load) == 2
