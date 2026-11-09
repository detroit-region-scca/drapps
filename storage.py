"""Where the app's data lives.

STORAGE_BACKEND=files  (default) keeps the original behaviour: NDJSON files under data/.
STORAGE_BACKEND=sheets keeps everything in Google Sheets in the region's treasurer Drive, so the
records outlive this app and this server. Every write to Sheets is also mirrored to the local NDJSON
files, which double as an offline backup and as the fallback if Google is unreachable.

The Sheets layout (one spreadsheet, REGISTRY_SHEET_ID):
  Events    id | program_code | event_name | date | sequence_id | unique_code | custom_tag | creator_name | cancelled | msr_url
  Audit     timestamp | action | user | details (JSON)
  Programs  code | name
"""
import json
import logging
import os
import threading

from dotenv import load_dotenv
from filelock import FileLock

# Read .env before any setting below: the backend is chosen at import, and it must agree with
# everything that checks STORAGE_BACKEND later (Docker sets these before start; local runs need this).
load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_FILE = os.environ.get("DATA_FILE", os.path.join(DATA_DIR, "events.ndjson"))
AUDIT_FILE = os.environ.get("AUDIT_FILE", os.path.join(DATA_DIR, "audit.ndjson"))
DATA_LOCK_FILE = os.environ.get("DATA_LOCK_FILE", f"{DATA_FILE}.lock")
PROGRAMS_FILE = os.environ.get("PROGRAMS_FILE", os.path.join(DATA_DIR, "programs.json"))

EVENT_COLS = ['id', 'program_code', 'event_name', 'date', 'sequence_id', 'unique_code', 'custom_tag', 'creator_name',
              'cancelled', 'msr_url']   # cancelled: date it was marked; msr_url: its MotorsportReg page, set by a person
CACHE_SECONDS = 15


def _load_ndjson(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.error(f"Malformed JSON in {path} at line {n}: {e}")
    return out


def _write_ndjson(path, rows):
    tmp = f"{path}.tmp"
    with open(tmp, 'w') as f:
        for r in rows:
            json.dump(r, f)
            f.write('\n')
    os.replace(tmp, path)


class StorageUnavailable(RuntimeError):
    """Google storage can't be reached; the page says so instead of failing."""


class FileStore:
    name = 'files'

    def load_events(self):
        return _load_ndjson(DATA_FILE)

    def save_events(self, events):
        _write_ndjson(DATA_FILE, events)

    def append_audit(self, entry):
        with FileLock(f"{AUDIT_FILE}.lock"):
            with open(AUDIT_FILE, 'a') as f:
                json.dump(entry, f)
                f.write('\n')

    def load_audit(self):
        return _load_ndjson(AUDIT_FILE)

    def load_programs(self):
        try:
            with open(PROGRAMS_FILE) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Error loading programs: {e}")
            return {}


_gc = None
_gc_lock = threading.Lock()


def sheets_client():
    """One authorised gspread client per process (service account with editor access to the sheets)."""
    global _gc
    with _gc_lock:
        if _gc is None:
            import gspread
            # BackOffHTTPClient retries 429s (Sheets allows 60 reads/min per user) with backoff.
            _gc = gspread.service_account(filename=os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], http_client=gspread.BackOffHTTPClient)
        return _gc


_books = {}


def open_book(sheet_id):
    """Spreadsheet handle, fetched once per process: every open costs one of the 60 reads a minute."""
    if sheet_id not in _books:
        _books[sheet_id] = sheets_client().open_by_key(sheet_id)
    return _books[sheet_id]


_tabs = {}


def worksheet(sheet_id, title):
    """Tab handle, also fetched once per process (looking a tab up by name is another read)."""
    if (sheet_id, title) not in _tabs:
        _tabs[(sheet_id, title)] = open_book(sheet_id).worksheet(title)
    return _tabs[(sheet_id, title)]


DRIVE = 'https://www.googleapis.com/drive/v3/files'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'


def drive_folder(name, parent):
    """Create a Drive folder (Shared Drive aware) and return {'id', 'webViewLink'}."""
    r = sheets_client().http_client.session.post(DRIVE, params={'supportsAllDrives': 'true', 'fields': 'id,webViewLink'},
                                                 json={'name': name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent]})
    r.raise_for_status()
    return r.json()


def drive_upload(f, parent):
    """Upload a Werkzeug FileStorage into a Drive folder. The service account has no storage of its own,
    so the parent must be in a Shared Drive."""
    files = {'metadata': ('metadata', json.dumps({'name': f.filename, 'parents': [parent]}), 'application/json'),
             'file': (f.filename, f.stream, f.mimetype or 'application/octet-stream')}
    r = sheets_client().http_client.session.post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true',
                                                                  'fields': 'id,webViewLink'}, files=files, timeout=110)
    r.raise_for_status()
    return r.json()


def drive_files(folder_id):
    """The files (not folders) in a Drive folder: [{'id', 'name', 'mimeType', 'md5Checksum'}]."""
    s, out, token = sheets_client().http_client.session, [], None
    while True:
        r = s.get(DRIVE, params={'q': f"'{folder_id}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'",
                                 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true', 'pageSize': 200,
                                 'fields': 'nextPageToken,files(id,name,mimeType,md5Checksum)', **({'pageToken': token} if token else {})})
        r.raise_for_status()
        out += r.json().get('files', [])
        token = r.json().get('nextPageToken')
        if not token:
            return out


def drive_download(file_id, path):
    """Save one Drive file's bytes to path."""
    r = sheets_client().http_client.session.get(f'{DRIVE}/{file_id}', params={'alt': 'media', 'supportsAllDrives': 'true'}, stream=True, timeout=110)
    r.raise_for_status()
    with open(path, 'wb') as f:
        for chunk in r.iter_content(1 << 16):
            f.write(chunk)


class SheetStore:
    name = 'sheets'

    def __init__(self, sheet_id):
        self.sheet_id = sheet_id
        self.files = FileStore()
        self._cache = {}

    def _ws(self, title):
        return worksheet(self.sheet_id, title)

    def _invalidate(self):
        import sheet_cache
        sheet_cache.invalidate(self.sheet_id)

    def _cached(self, key, loader):
        import sheet_cache
        # The version is read before loading: a write that lands while loading moves it on, so the next call
        # loads again instead of keeping the older answer.
        version = sheet_cache.version(self.sheet_id)
        hit = self._cache.get(key)
        if hit and hit[0] == version:
            return hit[1]
        val = loader()
        self._cache[key] = (version, val)
        return val

    def _records(self, rng, fresh=False):
        """A tab as a list of dicts keyed by its header row, as text (like get_all_records with numericise off),
        through the shared local copy (sheet_cache)."""
        import sheet_cache
        res = sheet_cache.batch_get(self.sheet_id, [rng], fresh=fresh)
        values = (res.get('valueRanges') or [{}])[0].get('values', [])
        if not values:
            return []
        head = [str(h) for h in values[0]]
        return [dict(zip(head, [str(v) for v in r] + [''] * (len(head) - len(r)))) for r in values[1:]]

    def load_events(self, fresh=False):
        # Page views read the shared local copy; anything that may write the list back (create, edit, delete
        # read, change and write the whole list) passes fresh=True, so another worker's write is never undone.
        try:
            rows = self._records('Events!A:J', fresh=fresh)
            self._degraded = False
            return [{k: (r.get(k) or '') for k in EVENT_COLS} for r in rows if r.get('id')]
        except Exception as e:
            logger.error(f"Sheets unreachable, using local copy of events: {e}")
            self._degraded = True
            return self.files.load_events()

    def save_events(self, events):
        if getattr(self, '_degraded', False):
            # The list came from the local mirror; writing it back could undo edits made in the sheet.
            raise StorageUnavailable('Google Sheets is unreachable, so events are read-only until it is back.')
        rows = [[str(e.get(c, '') or '') for c in EVENT_COLS] for e in sorted(events, key=lambda e: e.get('date', ''))]
        ws = self._ws('Events')
        # Write the new rows first and clear only what is left below them, so a failure part-way
        # can never leave the sheet empty.
        if len(rows) + 1 > ws.row_count:
            ws.add_rows(len(rows) + 1 - ws.row_count + 50)
        if ws.col_count < len(EVENT_COLS):
            ws.add_cols(len(EVENT_COLS) - ws.col_count)
        ws.update([EVENT_COLS], 'A1:J1', value_input_option='RAW')   # the header grows with EVENT_COLS
        if rows:
            ws.update(rows, f"A2:J{len(rows) + 1}", value_input_option='RAW')
        if ws.row_count > len(rows) + 1:
            ws.batch_clear([f"A{len(rows) + 2}:J{ws.row_count}"])
        self._invalidate()
        self.files.save_events(events)

    def append_audit(self, entry):
        self.files.append_audit(entry)
        try:
            import audit_view    # here, not at the top: audit_view imports flask helpers the storage layer does not need
            self._ws('Audit').append_row([entry['timestamp'], entry['action'], entry['user'], json.dumps(entry['details']),
                                          audit_view.plain(entry)], value_input_option='RAW')
        except Exception as e:
            logger.error(f"Audit entry kept locally only (Sheets write failed): {e}")
        self._cache.pop('audit', None)
        self._invalidate()

    def load_audit(self):
        def load():
            try:
                out = []
                for r in self._records('Audit!A:E'):
                    try:
                        details = json.loads(r.get('details') or '{}')
                    except json.JSONDecodeError:
                        details = {'raw': r.get('details')}
                    out.append({'timestamp': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', ''), 'details': details})
                return out
            except Exception as e:
                logger.error(f"Sheets unreachable, using local audit log: {e}")
                return self.files.load_audit()
        return self._cached('audit', load)

    def save_program(self, code, name):
        """Rename a program, or add one (code, name) to the Programs tab."""
        ws = self._ws('Programs')
        codes = [str(c).strip() for c in ws.col_values(1)]
        if code in codes:
            ws.update([[code, name]], f'A{codes.index(code) + 1}', value_input_option='RAW')
        else:
            ws.append_row([code, name], value_input_option='RAW', table_range='A1')
        self._invalidate()

    def load_programs(self):
        def load():
            try:
                return {str(r['code']): str(r['name']) for r in self._records('Programs!A:B') if r.get('code')}
            except Exception as e:
                logger.error(f"Sheets unreachable, using local programs file: {e}")
                return self.files.load_programs()
        return self._cached('programs', load)


def make_store():
    if os.environ.get('STORAGE_BACKEND', 'files') == 'sheets':
        return SheetStore(os.environ['REGISTRY_SHEET_ID'])
    return FileStore()


store = make_store()
