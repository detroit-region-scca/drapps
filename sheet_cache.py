"""A fast local copy of the Google Sheets, shared by every gunicorn worker (SQLite in data/).

Google Sheets stays the source of truth: this copy is disposable (delete the file and it rebuilds from Sheets).

- Reads (batch_get) answer from the copy, so a page never waits on Google once the copy exists.
- Every write path calls invalidate(sheet_id) (through each store's refresh()), so the next read of that sheet
  fetches fresh from Google, in every worker.
- A background poller (one worker at a time, chosen by a file lock) asks Drive for each sheet's modifiedTime every
  POLL_SECONDS and re-downloads only the sheets that changed, so edits made straight in Sheets (or by the nightly
  QuickBooks sync) show up within about that long.
- version(sheet_id) changes whenever a sheet's copy changes; stores keep their parsed snapshot in memory until it does.

Reads that a write depends on (finding a row to update, the next id) stay live; they never come through here.
"""
import json
import logging
import os
import sqlite3
import threading
import time

from filelock import FileLock, Timeout

from storage import DATA_DIR, open_book, sheets_client

log = logging.getLogger(__name__)

DB = os.environ.get('SHEET_CACHE_DB', os.path.join(DATA_DIR, 'sheet_cache.sqlite'))
POLL_SECONDS = int(os.environ.get('SHEET_CACHE_POLL_SECONDS', '15'))
ERROR_SECONDS = 300      # a remembered refusal (missing tab, lost access) is retried after this long
LEADER_LOCK = os.path.join(DATA_DIR, 'sheet_cache.leader.lock')
DRIVE = 'https://www.googleapis.com/drive/v3/files/'

_local = threading.local()
_poller = None
_poller_lock = threading.Lock()
_hand_logged = {}          # (sheet, person) -> when their last hand edit was logged
HAND_EDIT_QUIET = 30 * 60


def enabled():
    return os.environ.get('STORAGE_BACKEND') == 'sheets' and os.environ.get('SHEET_CACHE', '1') != '0'


def _db():
    c = getattr(_local, 'db', None)
    if c is None:
        c = sqlite3.connect(DB, timeout=15, isolation_level=None)
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA synchronous=NORMAL')
        c.execute('CREATE TABLE IF NOT EXISTS snaps (sheet_id TEXT, key TEXT, ranges TEXT, params TEXT, data TEXT, error TEXT,'
                  ' stale INTEGER DEFAULT 0, fetched REAL, PRIMARY KEY (sheet_id, key))')
        c.execute('CREATE TABLE IF NOT EXISTS sheets (sheet_id TEXT PRIMARY KEY, version INTEGER DEFAULT 0, modified TEXT DEFAULT "",'
                  ' checked REAL DEFAULT 0, writes INTEGER DEFAULT 0)')
        if 'writes' not in [r[1] for r in c.execute('PRAGMA table_info(sheets)')]:      # a copy made before this column
            try:
                c.execute('ALTER TABLE sheets ADD COLUMN writes INTEGER DEFAULT 0')
            except sqlite3.OperationalError:
                pass                                                                    # another worker just added it
        _local.db = c
    return c


class CachedAPIError(Exception):
    """A read that Google refused (a missing tab, say), remembered so it is not retried on every page."""


def _api_error(msg):
    """Re-raise a remembered refusal as gspread's APIError, which callers already catch."""
    import gspread

    class _Resp:
        text = msg

        def json(self):
            return {'error': {'code': 400, 'message': msg, 'status': 'CACHED'}}
    return gspread.exceptions.APIError(_Resp())


def _bump(c, sheet_id):
    c.execute('INSERT INTO sheets (sheet_id, version) VALUES (?, 1) ON CONFLICT(sheet_id) DO UPDATE SET version = version + 1',
              (sheet_id,))


def _writes(c, sheet_id):
    """How many times the apps have written to this sheet (invalidate calls)."""
    row = c.execute('SELECT writes FROM sheets WHERE sheet_id = ?', (sheet_id,)).fetchone()
    return (row[0] or 0) if row else 0


def _fetch(sheet_id, key, ranges, params):
    """Read from Google and store it (or the refusal). Returns (data, error message). A write that lands while Google
    is answering may not be in the answer, so the stored copy is then marked stale and the next read asks again."""
    import gspread
    before = _writes(_db(), sheet_id)
    try:
        data, err = open_book(sheet_id).values_batch_get(ranges, params=params), None
    except gspread.exceptions.APIError as e:
        if getattr(e, 'code', 0) in (429, 500, 502, 503):     # quota or Google trouble: do not remember it
            raise
        data, err = None, str(e)
    c = _db()
    c.execute('BEGIN IMMEDIATE')
    try:
        stale = 1 if _writes(c, sheet_id) != before else 0
        c.execute('INSERT OR REPLACE INTO snaps (sheet_id, key, ranges, params, data, error, stale, fetched) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                  (sheet_id, key, json.dumps(ranges), json.dumps(params), None if data is None else json.dumps(data), err, stale, time.time()))
        _bump(c, sheet_id)
        c.execute('COMMIT')
    except Exception:
        c.execute('ROLLBACK')
        raise
    return data, err


def batch_get(sheet_id, ranges, params=None, fresh=False):
    """Same result as open_book(sheet_id).values_batch_get(ranges, params=params), from the local copy when it has one."""
    if not enabled():
        return open_book(sheet_id).values_batch_get(ranges, params=params)
    _start_poller()
    key = json.dumps([ranges, params], sort_keys=True)
    if not fresh:
        row = _db().execute('SELECT data, error, stale, fetched FROM snaps WHERE sheet_id = ? AND key = ?', (sheet_id, key)).fetchone()
        if row and not row[2]:
            if not row[1]:
                return json.loads(row[0])
            if time.time() - (row[3] or 0) < ERROR_SECONDS:
                raise _api_error(row[1])
            # an old refusal: ask Google again (the tab may exist now, or access may be back)
    data, err = _fetch(sheet_id, key, ranges, params)
    if err:
        raise _api_error(err)
    return data


def invalidate(sheet_id):
    """After (or before) a write: the next read of this sheet, in any worker, goes to Google."""
    if not enabled() or not sheet_id:
        return
    c = _db()
    c.execute('BEGIN IMMEDIATE')
    try:
        c.execute('UPDATE snaps SET stale = 1 WHERE sheet_id = ?', (sheet_id,))
        _bump(c, sheet_id)
        c.execute('UPDATE sheets SET writes = COALESCE(writes, 0) + 1 WHERE sheet_id = ?', (sheet_id,))
        c.execute('COMMIT')
    except Exception:
        c.execute('ROLLBACK')
        raise


def version(sheet_id):
    """Changes whenever the local copy of this sheet changes (a fetch or an invalidate). 0 when caching is off."""
    if not enabled():
        return time.time() // 15          # caching off: behave like the old 15-second memory cache
    row = _db().execute('SELECT version FROM sheets WHERE sheet_id = ?', (sheet_id,)).fetchone()
    return row[0] if row else 0


# ---------- other slow Google answers (Drive folder listings), shared for a few minutes ----------

def remember(key, ttl, loader):
    """loader()'s JSON-able result, shared by all workers for ttl seconds."""
    if not enabled():
        return loader()
    c = _db()
    c.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, data TEXT, saved REAL)')
    row = c.execute('SELECT data, saved FROM kv WHERE key = ?', (key,)).fetchone()
    if row and time.time() - row[1] < ttl:
        return json.loads(row[0])
    val = loader()
    c.execute('INSERT OR REPLACE INTO kv (key, data, saved) VALUES (?, ?, ?)', (key, json.dumps(val), time.time()))
    return val


def forget(prefix):
    """Drop remembered answers whose key starts with prefix (after a change they depend on)."""
    if enabled():
        c = _db()
        c.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, data TEXT, saved REAL)')
        c.execute('DELETE FROM kv WHERE key LIKE ?', (prefix + '%',))


# ---------- keeping the copy current ----------

class SheetGone(Exception):
    """Drive says the sheet does not exist (deleted, or no longer shared with the app)."""


def _modified(sheet_id):
    """Drive's modifiedTime for a sheet, plus its name and who changed it last."""
    r = sheets_client().http_client.session.get(DRIVE + sheet_id, params={'fields': 'modifiedTime,name,lastModifyingUser(displayName,emailAddress)',
                                                                         'supportsAllDrives': 'true'}, timeout=20)
    if r.status_code == 404:
        raise SheetGone(sheet_id)
    r.raise_for_status()
    return r.json()


def _note_hand_edit(sheet_id, info):
    """A sheet changed by a person rather than by the apps (which write as a service account): say so in the audit
    log, with a link to the sheet, whose version history shows exactly what changed."""
    who = info.get('lastModifyingUser') or {}
    mail = (who.get('emailAddress') or '').lower()
    if not mail or mail.endswith('.gserviceaccount.com'):
        return
    # One entry per person per sheet per half hour: an editing session is one line in the log, not one per poll.
    now = time.time()
    if now - _hand_logged.get((sheet_id, mail), 0) < HAND_EDIT_QUIET:
        return
    _hand_logged[(sheet_id, mail)] = now
    try:
        from utils import log_audit_event
        log_audit_event('SHEET_EDITED', who.get('displayName') or mail,
                        {'sheet': info.get('name', ''), 'email': mail, 'modified': info.get('modifiedTime', ''),
                         'link': f'https://docs.google.com/spreadsheets/d/{sheet_id}/edit'})
    except Exception as e:
        log.warning('sheet cache: could not log a hand edit of %s: %s', sheet_id, e)


def poll_once():
    """Re-download every stored read of each sheet whose Drive modifiedTime changed since the last look."""
    c = _db()
    for (sheet_id, last) in c.execute('SELECT DISTINCT s.sheet_id, COALESCE(m.modified, "") FROM snaps s'
                                      ' LEFT JOIN sheets m ON m.sheet_id = s.sheet_id').fetchall():
        try:
            info = _modified(sheet_id)
        except SheetGone:
            # Stop checking a sheet that is gone; if a page asks for it again it is fetched (and fails) afresh.
            log.warning('sheet cache: %s no longer exists in Drive; dropped from the local copy', sheet_id)
            c.execute('DELETE FROM snaps WHERE sheet_id = ?', (sheet_id,))
            _bump(c, sheet_id)
            continue
        except Exception as e:
            log.warning('sheet cache: modifiedTime for %s failed: %s', sheet_id, e)
            continue
        mod = info.get('modifiedTime', '')
        if mod == last:
            continue
        if last:            # not the first look at this sheet
            _note_hand_edit(sheet_id, info)
        for key, ranges, params in c.execute('SELECT key, ranges, params FROM snaps WHERE sheet_id = ?', (sheet_id,)).fetchall():
            try:
                _fetch(sheet_id, key, json.loads(ranges), json.loads(params))
            except Exception as e:
                log.warning('sheet cache: refresh of %s failed: %s', sheet_id, e)
                break
        else:
            c.execute('UPDATE sheets SET modified = ?, checked = ? WHERE sheet_id = ?', (mod, time.time(), sheet_id))


def _poll_forever():
    lock = FileLock(LEADER_LOCK)
    complained = False
    while True:
        try:
            lock.acquire(timeout=0)          # only one worker polls; the others try again later in case it died
        except Timeout:
            time.sleep(POLL_SECONDS * 4)
            continue
        except Exception as e:
            # The lock file can't be opened (owned by root after a job run as root, a full disk): keep trying
            # instead of letting the thread die, or edits made straight in Sheets would never show up again.
            if not complained:
                log.error('sheet cache: cannot take %s (%s); edits made in Sheets will not show until this is fixed', LEADER_LOCK, e)
                complained = True
            time.sleep(POLL_SECONDS * 4)
            continue
        complained = False
        try:
            while True:
                try:
                    poll_once()
                except Exception as e:
                    log.warning('sheet cache: poll failed: %s', e)
                time.sleep(POLL_SECONDS)
        finally:
            try:
                lock.release()
            except Exception as e:
                log.warning('sheet cache: could not release %s: %s', LEADER_LOCK, e)


def _start_poller():
    global _poller
    if _poller is not None or POLL_SECONDS <= 0:
        return
    with _poller_lock:
        if _poller is None:
            _poller = threading.Thread(target=_poll_forever, name='sheet-cache-poller', daemon=True)
            _poller.start()
