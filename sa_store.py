"""Service awards sheet (SERVICE_AWARDS_SHEET_ID, tab "Awards"). The sheet is the source of truth; edit it by hand freely."""
import os
import threading

from storage import worksheet
import sa_logic as L

TAB = 'Awards'
_lock = threading.Lock()


def enabled():
    return bool(os.environ.get('SERVICE_AWARDS_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def sheet_url():
    return f"https://docs.google.com/spreadsheets/d/{os.environ.get('SERVICE_AWARDS_SHEET_ID', '')}/edit"


def _ws():
    return worksheet(os.environ['SERVICE_AWARDS_SHEET_ID'], TAB)


def rows():
    """Data rows, read live (the sheet may have been edited by hand a moment ago)."""
    vals = _ws().get_all_values()
    return [r for r in vals[1:] if any(str(c).strip() for c in r)]


def save_month(month, new_rows):
    """Replace one month's rows and rewrite the tab in date order. Returns how many rows the month now has."""
    with _lock:
        ws = _ws()
        out = L.replace_month(rows(), month, new_rows)
        ws.batch_clear(['A2:D'])
        if out:
            ws.update([[int(c) if i == 2 and str(c).strip().isdigit() else str(c) for i, c in enumerate((list(r) + [''] * 4)[:4])] for r in out],
                      'A2', value_input_option='RAW')
    return len(new_rows)


# ---------- the SCCA member list files (private folder in the DRSCCA Apps drive) ----------

_roster_cache = {}


def roster_enabled():
    return bool(os.environ.get('MEMBER_LISTS_FOLDER_ID'))


def _session():
    from storage import sheets_client
    return sheets_client().http_client.session


def save_roster(data, uploaded_by):
    """Keep the original report in Drive, named by date, and return its Drive file info."""
    import json as _json
    from datetime import date
    from storage import UPLOAD
    name = f'{date.today().isoformat()} SCCA Member List.xlsx'
    files = {'metadata': ('metadata', _json.dumps({'name': name, 'parents': [os.environ['MEMBER_LISTS_FOLDER_ID']],
                                                    'description': f'Uploaded in drapps by {uploaded_by}'}), 'application/json'),
             'file': (name, data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
    r = _session().post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true',
                                        'fields': 'id,name,createdTime,webViewLink'}, files=files)
    r.raise_for_status()
    return r.json()


def latest_roster():
    """Newest member list in the folder: {id, name, createdTime, webViewLink, members}; None if there is none."""
    from storage import DRIVE
    r = _session().get(DRIVE, params={'q': f"'{os.environ['MEMBER_LISTS_FOLDER_ID']}' in parents and trashed=false",
                                      'orderBy': 'createdTime desc', 'pageSize': 1, 'supportsAllDrives': 'true',
                                      'includeItemsFromAllDrives': 'true', 'fields': 'files(id,name,createdTime,webViewLink)'})
    r.raise_for_status()
    files = r.json().get('files', [])
    if not files:
        return None
    f = files[0]
    if f['id'] not in _roster_cache:
        data = _session().get(f"{DRIVE}/{f['id']}", params={'alt': 'media', 'supportsAllDrives': 'true'})
        data.raise_for_status()
        _roster_cache.clear()
        _roster_cache[f['id']] = L.read_roster(data.content)
    return dict(f, members=_roster_cache[f['id']])
