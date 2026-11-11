"""Hassles, kept in the Registry sheet (REGISTRY_SHEET_ID) like the rest of the app's records: the 'Hassles' tab, one
row per hassle, and the 'Hassle replies' tab, one row per reply. Reads go through the sheet cache; every write clears
it, so the next page shows the change. Both tabs are made the first time something is written.

Hassles: id | created | by | by_email | title | details | status | note | votes (emails, one per line)
Hassle replies: hassle_id | at | by | by_email | text
"""
import os
import threading
from datetime import date, datetime

import gspread
from filelock import FileLock

import sheet_cache as C
from storage import DATA_DIR, open_book, worksheet

TAB, REPLIES = 'Hassles', 'Hassle replies'
COLS = ['id', 'created', 'by', 'by_email', 'title', 'details', 'status', 'note', 'votes']
RCOLS = ['hassle_id', 'at', 'by', 'by_email', 'text']
STATUSES = ['Open', 'Planned', 'Fixed', 'Not doing']
SAVE_LOCK = os.path.join(DATA_DIR, 'hassles_save.lock')
_lock = threading.Lock()


def enabled():
    return bool(os.environ.get('REGISTRY_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _sheet():
    return os.environ['REGISTRY_SHEET_ID']


def _ws(tab, cols):
    try:
        return worksheet(_sheet(), tab)
    except gspread.exceptions.WorksheetNotFound:
        ws = open_book(_sheet()).add_worksheet(tab, rows=200, cols=len(cols))
        ws.update([cols], 'A1', value_input_option='RAW')
        return ws


def _text(v):
    return '' if v is None else str(v)


def _rows(res, i, cols):
    vals = (res.get('valueRanges') or [{}] * 2)[i].get('values', []) if len(res.get('valueRanges') or []) > i else []
    return [{c: _text(v) for c, v in zip(cols, list(r) + [''] * len(cols))} for r in vals[1:] if r and str(r[0]).strip()]


def hassles():
    """Every hassle with its replies (oldest reply first): [{..., 'id': int, 'votes': [emails], 'replies': [...]}]."""
    try:
        res = C.batch_get(_sheet(), [f"'{TAB}'!A:I", f"'{REPLIES}'!A:E"], params={'valueRenderOption': 'FORMATTED_VALUE'})
    except gspread.exceptions.APIError:
        res = None
    if res is None:          # a tab is missing until the first write: read the one that exists
        try:
            res = C.batch_get(_sheet(), [f"'{TAB}'!A:I"], params={'valueRenderOption': 'FORMATTED_VALUE'})
        except gspread.exceptions.APIError:
            return []
    out, by_id = [], {}
    for h in _rows(res, 0, COLS):
        if not h['id'].isdigit():
            continue
        h['id'] = int(h['id'])
        h['votes'] = [v.strip().lower() for v in h['votes'].splitlines() if v.strip()]
        h['status'] = h['status'] if h['status'] in STATUSES else 'Open'
        h['by_email'] = h['by_email'].lower()
        h['replies'] = []
        out.append(h)
        by_id[h['id']] = h
    for r in _rows(res, 1, RCOLS):
        if r['hassle_id'].isdigit() and int(r['hassle_id']) in by_id:
            r['by_email'] = r['by_email'].lower()
            by_id[int(r['hassle_id'])]['replies'].append(r)
    for h in out:
        h['replies'].sort(key=lambda r: r['at'])
    return out


def get(hid):
    return next((h for h in hassles() if h['id'] == hid), None)


def _row_of(ws, hid):
    ids = ws.col_values(1)
    return ids.index(str(hid)) + 1 if str(hid) in ids else None


def add(title, details, by, by_email):
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws(TAB, COLS)
        ids = [int(i) for i in ws.col_values(1)[1:] if str(i).isdigit()]
        hid = max(ids or [0]) + 1
        ws.append_row([str(hid), date.today().isoformat(), by, by_email, title, details, 'Open', '', ''],
                      value_input_option='RAW', table_range='A1')
        _ws(REPLIES, RCOLS)          # made now, so reads of both tabs work from the first hassle on
        C.invalidate(_sheet())
        return hid


def update(hid, **fields):
    """Change some columns of one hassle (votes as a list). False if it is gone."""
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws(TAB, COLS)
        row = _row_of(ws, hid)
        if not row:
            return False
        upd = [{'range': f'{chr(65 + COLS.index(k))}{row}', 'values': [['\n'.join(v) if k == 'votes' else v]]}
               for k, v in fields.items() if k in COLS and k != 'id']
        ws.batch_update(upd, value_input_option='RAW')
        C.invalidate(_sheet())
        return True


def toggle_vote(hid, email):
    """Thumbs up, or take it back, read and written under the lock so two clicks at once both count."""
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws(TAB, COLS)
        row = _row_of(ws, hid)
        if not row:
            return False
        cell = ws.acell(f'I{row}').value or ''
        votes = [v.strip().lower() for v in cell.splitlines() if v.strip()]
        votes = [v for v in votes if v != email] if email in votes else votes + [email]
        ws.update([['\n'.join(votes)]], f'I{row}', value_input_option='RAW')
        C.invalidate(_sheet())
        return True


def delete(hid):
    """Remove a hassle and its replies."""
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws(TAB, COLS)
        row = _row_of(ws, hid)
        if row:
            ws.delete_rows(row)
        rws = _ws(REPLIES, RCOLS)
        for i in sorted((n + 1 for n, v in enumerate(rws.col_values(1)) if n and v == str(hid)), reverse=True):
            rws.delete_rows(i)
        C.invalidate(_sheet())


def reply(hid, text, by, by_email):
    with FileLock(SAVE_LOCK), _lock:
        _ws(REPLIES, RCOLS).append_row([str(hid), datetime.now().strftime('%Y-%m-%d %H:%M'), by, by_email, text],
                                        value_input_option='RAW', table_range='A1')
        C.invalidate(_sheet())


def delete_reply(hid, at, by_email):
    """Remove one reply, found by its hassle, time and author."""
    with FileLock(SAVE_LOCK), _lock:
        rws = _ws(REPLIES, RCOLS)
        for n, r in enumerate(rws.get_all_values()):
            if n and r[:1] == [str(hid)] and len(r) > 3 and r[1] == at and r[3].lower() == by_email:
                rws.delete_rows(n + 1)
                break
        C.invalidate(_sheet())
