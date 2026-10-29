"""Where the app's data lives.

STORAGE_BACKEND=files  (default) keeps the original behaviour: NDJSON files under data/.
STORAGE_BACKEND=sheets keeps everything in Google Sheets in the region's treasurer Drive, so the
records outlive this app and this server. Every write to Sheets is also mirrored to the local NDJSON
files, which double as an offline backup and as the fallback if Google is unreachable.

The Sheets layout (one spreadsheet, REGISTRY_SHEET_ID):
  Events    id | program_code | event_name | date | sequence_id | unique_code | custom_tag | creator_name
  Audit     timestamp | action | user | details (JSON)
  Programs  code | name
"""
import json
import logging
import os
import threading
import time

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

EVENT_COLS = ['id', 'program_code', 'event_name', 'date', 'sequence_id', 'unique_code', 'custom_tag', 'creator_name']
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
    r = sheets_client().http_client.session.post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true'}, files=files)
    r.raise_for_status()


class SheetStore:
    name = 'sheets'

    def __init__(self, sheet_id):
        self.sheet_id = sheet_id
        self.files = FileStore()
        self._cache = {}

    def _ws(self, title):
        return worksheet(self.sheet_id, title)

    def _cached(self, key, loader):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        val = loader()
        self._cache[key] = (time.time(), val)
        return val

    def load_events(self):
        # Never cached: create/edit/delete read, change and write the whole list, and gunicorn runs
        # several workers, so a cached copy in one worker would silently undo another worker's write.
        try:
            rows = self._ws('Events').get_all_records(expected_headers=EVENT_COLS, numericise_ignore=['all'])
            self._degraded = False
            return [{k: (r.get(k) or '') for k in EVENT_COLS} for r in rows if r.get('id')]
        except Exception as e:
            logger.error(f"Sheets unreachable, using local copy of events: {e}")
            self._degraded = True
            return self.files.load_events()

    def save_events(self, events):
        if getattr(self, '_degraded', False):
            # The list came from the local mirror; writing it back could undo edits made in the sheet.
            raise RuntimeError('Google Sheets is unreachable, so events are read-only until it is back.')
        rows = [[str(e.get(c, '') or '') for c in EVENT_COLS] for e in sorted(events, key=lambda e: e.get('date', ''))]
        ws = self._ws('Events')
        # Write the new rows first and clear only what is left below them, so a failure part-way
        # can never leave the sheet empty.
        if len(rows) + 1 > ws.row_count:
            ws.add_rows(len(rows) + 1 - ws.row_count + 50)
        if rows:
            ws.update(rows, f"A2:H{len(rows) + 1}", value_input_option='RAW')
        if ws.row_count > len(rows) + 1:
            ws.batch_clear([f"A{len(rows) + 2}:H{ws.row_count}"])
        self.files.save_events(events)

    def append_audit(self, entry):
        self.files.append_audit(entry)
        try:
            self._ws('Audit').append_row([entry['timestamp'], entry['action'], entry['user'], json.dumps(entry['details'])],
                                         value_input_option='RAW')
        except Exception as e:
            logger.error(f"Audit entry kept locally only (Sheets write failed): {e}")
        self._cache.pop('audit', None)

    def load_audit(self):
        def load():
            try:
                out = []
                for r in self._ws('Audit').get_all_records(numericise_ignore=['all']):
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

    def load_programs(self):
        def load():
            try:
                return {str(r['code']): str(r['name']) for r in self._ws('Programs').get_all_records(numericise_ignore=['all']) if r.get('code')}
            except Exception as e:
                logger.error(f"Sheets unreachable, using local programs file: {e}")
                return self.files.load_programs()
        return self._cached('programs', load)


def make_store():
    if os.environ.get('STORAGE_BACKEND', 'files') == 'sheets':
        return SheetStore(os.environ['REGISTRY_SHEET_ID'])
    return FileStore()


store = make_store()
