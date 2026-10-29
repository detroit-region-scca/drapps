"""Reads and writes the Funds Requests Google Sheet (FUNDS_SHEET_ID) in the DRSCCA Apps Shared Drive.

The sheet is the record and stays usable by hand: one row per request on Requests, one row per expense
on Lines (joined by id), receipts in a Drive folder per request under FUNDS_RECEIPTS_FOLDER_ID.
Receipts need the Shared Drive: a service account has no storage of its own, so it cannot own files
in anyone's My Drive.
"""
import os
import re
import threading
import time
from datetime import date

from filelock import FileLock

from storage import DATA_DIR, drive_folder, drive_upload, open_book, worksheet

REQ_COLS = ['id', 'submitted', 'status', 'event_code', 'event_name', 'program', 'submitter_name', 'submitter_email',
            'submitter_phone', 'submitter_address', 'payee_name', 'payee_address', 'payee_email', 'payee_phone', 'method',
            'total', 'advances', 'net', 'notes', 'receipts_url', 'entered_by', 'paid_date', 'paid_ref', 'paid_amount',
            'closed_by', 'closed_note', 'approvals']
LINE_COLS = ['request_id', 'date', 'description', 'category', 'amount']
NUMERIC = ('total', 'advances', 'net', 'paid_amount')
CATEGORIES = ['Site Rental', 'Supplies', 'Professional Service', 'Storage', 'Travel Cost', 'Other']
METHODS = {'check': 'Check to the payee', 'card': 'Region credit card', 'reimburse': 'Reimburse me'}
CACHE_SECONDS = 15
ID_LOCK = os.path.join(DATA_DIR, 'funds_id.lock')

_lock = threading.Lock()
_cache = {}


def enabled():
    return bool(os.environ.get('FUNDS_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _ws(title):
    return worksheet(os.environ['FUNDS_SHEET_ID'], title)


def refresh():
    """Drop the cached snapshot; every write path calls this first (several gunicorn workers)."""
    _cache.clear()


def _snapshot():
    hit = _cache.get('snap')
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    res = open_book(os.environ['FUNDS_SHEET_ID']).values_batch_get(['Requests!A:AA', 'Lines!A:E'],
                                                                   params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    vr = [r.get('values', []) for r in res.get('valueRanges', [])] + [[], []]
    reqs = [dict(zip(REQ_COLS, [str(v) for v in r] + [''] * len(REQ_COLS))) for r in vr[0][1:] if r and str(r[0]).strip()]
    lines = [dict(zip(LINE_COLS, list(r) + [''] * len(LINE_COLS))) for r in vr[1][1:] if r and str(r[0]).strip()]
    for r in reqs:
        for k in NUMERIC:
            r[k] = _num(r[k])
    for ln in lines:
        ln['amount'] = _num(ln['amount'])
        ln['date'] = str(ln['date'])
    snap = (reqs, lines)
    _cache['snap'] = (time.time(), snap)
    return snap


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def requests():
    return _snapshot()[0]


def get(req_id):
    reqs, lines = _snapshot()
    r = next((r for r in reqs if r['id'] == req_id), None)
    if r:
        r = dict(r, lines=[ln for ln in lines if ln['request_id'] == req_id])
    return r


def _next_id(year):
    prefix = f'FR-{year}-'
    nums = [int(v[len(prefix):]) for v in _ws('Requests').col_values(1) if v.startswith(prefix) and v[len(prefix):].isdigit()]
    return f'{prefix}{max(nums, default=0) + 1:03d}'


def create(req, lines, receipts):
    """req: dict of REQ_COLS fields (without id/submitted/status/receipts_url). Returns the new id."""
    with FileLock(ID_LOCK), _lock:
        rid = _next_id(date.today().year)
        receipts = [f for f in receipts if f and f.filename]
        url = ''
        if receipts:
            folder = drive_folder(f"{rid} - {req['payee_name']}"[:120], os.environ['FUNDS_RECEIPTS_FOLDER_ID'])
            for f in receipts:
                drive_upload(f, folder['id'])
            url = folder['webViewLink']
        row = dict(req, id=rid, submitted=date.today().isoformat(), status='Submitted', receipts_url=url)
        _ws('Requests').append_row([_cell(row.get(c, '')) for c in REQ_COLS], value_input_option='RAW')
        _ws('Lines').append_rows([[rid, ln['date'], ln['description'], ln['category'], ln['amount']] for ln in lines],
                                 value_input_option='RAW')
        refresh()
        return rid


def _cell(v):
    return v if isinstance(v, (int, float)) else str(v or '')


def update(req_id, **fields):
    """Change fields on one request (re-reads the row so concurrent edits to other columns survive)."""
    with _lock:
        ws = _ws('Requests')
        row = ws.col_values(1).index(req_id) + 1
        cur = dict(zip(REQ_COLS, ws.row_values(row) + [''] * len(REQ_COLS)))
        cur.update(fields)
        for k in NUMERIC:
            cur[k] = _num(cur[k]) if cur[k] != '' else ''
        ws.update([[_cell(cur[c]) for c in REQ_COLS]], f'A{row}', value_input_option='RAW')
        refresh()
        return cur


def close(req_id, status, **fields):
    """Mark a request Paid or Rejected (fields: paid_date, paid_ref, paid_amount, closed_by, closed_note)."""
    return update(req_id, status=status, **fields)


def add_receipts(req_id, files):
    """Upload more receipts into the request's Drive folder (made now if it had none). Returns how many."""
    files = [f for f in files if f and f.filename]
    if not files:
        return 0
    r = get(req_id)
    m = re.search(r'/folders/([\w-]+)', r.get('receipts_url') or '')
    folder_id, url = (m.group(1), r['receipts_url']) if m else (None, '')
    if not folder_id:
        folder = drive_folder(f"{req_id} - {r['payee_name']}"[:120], os.environ['FUNDS_RECEIPTS_FOLDER_ID'])
        folder_id, url = folder['id'], folder['webViewLink']
    for f in files:
        drive_upload(f, folder_id)
    if url != r.get('receipts_url'):
        update(req_id, receipts_url=url)
    return len(files)
