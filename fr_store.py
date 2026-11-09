"""Reads and writes the Funds Requests Google Sheet (FUNDS_SHEET_ID) in the DRSCCA Apps Shared Drive.

The sheet is the record and stays usable by hand: one row per request on Requests, one row per expense
on Lines (joined by id), receipts in a Drive folder per request under FUNDS_RECEIPTS_FOLDER_ID.
Receipts need the Shared Drive: a service account has no storage of its own, so it cannot own files
in anyone's My Drive.
"""
import json
import os
import re
import threading
import time
from datetime import date

from filelock import FileLock

import fr_logic
from storage import DATA_DIR, drive_folder, drive_upload, worksheet
import sheet_cache as C

REQ_COLS = ['id', 'submitted', 'status', 'event_code', 'event_name', 'program', 'submitter_name', 'submitter_email',
            'submitter_phone', 'submitter_address', 'payee_name', 'payee_address', 'payee_email', 'payee_phone', 'method',
            'total', 'advances', 'net', 'notes', 'receipts_url', 'entered_by', 'paid_date', 'paid_ref', 'paid_amount',
            'closed_by', 'closed_note', 'approvals', 'notify']
LINE_COLS = ['request_id', 'date', 'description', 'category', 'amount']
NUMERIC = ('total', 'advances', 'net', 'paid_amount')
CATEGORIES = ['Site Rental', 'Supplies', 'Professional Service', 'Storage', 'Travel Cost', 'Other']
METHODS = {'check': 'Check to the payee', 'card': 'Region credit card', 'reimburse': 'Reimburse me'}
ID_LOCK = os.path.join(DATA_DIR, 'funds_id.lock')
# The New request pages already used ({token: {'id', 'at'}}): a double click, the back button or a retry after an
# error part-way never files a second request (as for invoices, inv_store).
ISSUED_FILE = os.path.join(DATA_DIR, 'funds-issued.json')

_lock = threading.Lock()
_cache = {}


def enabled():
    return bool(os.environ.get('FUNDS_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _ws(title):
    return worksheet(os.environ['FUNDS_SHEET_ID'], title)


def refresh():
    """Drop the cached snapshot; every write path calls this first (several gunicorn workers)."""
    _cache.clear()
    C.invalidate(os.environ['FUNDS_SHEET_ID'])


def _snapshot():
    hit = _cache.get('snap')
    if hit and hit[0] == C.version(os.environ['FUNDS_SHEET_ID']):
        return hit[1]
    res = C.batch_get(os.environ['FUNDS_SHEET_ID'], ['Requests!A:AB', 'Lines!A:E'],
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
    _cache['snap'] = (C.version(os.environ['FUNDS_SHEET_ID']), snap)
    return snap


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def requests():
    """Every request except deleted ones."""
    return [r for r in _snapshot()[0] if r['status'] != 'Deleted']


def get(req_id):
    reqs, lines = _snapshot()
    r = next((r for r in reqs if r['id'] == req_id and r['status'] != 'Deleted'), None)
    if r:
        r = dict(r, lines=[ln for ln in lines if ln['request_id'] == req_id])
    return r


def _next_id(year):
    prefix = f'FR-{year}-'
    nums = [int(v[len(prefix):]) for v in _ws('Requests').col_values(1) if v.startswith(prefix) and v[len(prefix):].isdigit()]
    return f'{prefix}{max(nums, default=0) + 1:03d}'


def _issued():
    try:
        with open(ISSUED_FILE) as f:
            return dict(json.load(f))
    except (OSError, ValueError):
        return {}


def _save_issued(d):
    now = time.time()
    d = {k: v for k, v in d.items() if now - v.get('at', 0) < 90 * 86400}
    tmp = ISSUED_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f)
    os.replace(tmp, ISSUED_FILE)


def _line_rows(rid, lines):
    return [[rid, ln['date'], ln['description'], ln['category'], ln['amount']] for ln in lines]


def create_once(req, lines, receipts, token=''):
    """req: dict of REQ_COLS fields (without id/submitted/status/receipts_url). token: the New request page's one-time
    token. Returns (id, True) when that page already filed one (nothing new is filed), else (the new id, False). If the
    first try stopped before its expense lines were written, they are written now and it counts as new, so the
    emails and the audit entry the first try never reached still happen."""
    with FileLock(ID_LOCK), _lock:
        issued = _issued()
        if token and token in issued:
            refresh()
            done = get(issued[token]['id'])
            if done:
                if not done['lines']:      # the first try stopped part-way, before anyone was told: finish it as new
                    _ws('Lines').append_rows(_line_rows(done['id'], lines), value_input_option='RAW')
                    refresh()
                    return done['id'], False
                return done['id'], True
        rid = _next_id(date.today().year)
        receipts = [f for f in receipts if f and f.filename]
        url = ''
        if receipts:
            folder = drive_folder(f"{rid} - {req['payee_name']}"[:120], os.environ['FUNDS_RECEIPTS_FOLDER_ID'])
            for f in receipts:
                drive_upload(f, folder['id'])
            url = folder['webViewLink']
        row = dict(req, id=rid, submitted=date.today().isoformat(), status='Submitted', receipts_url=url)
        if token:      # recorded before the writes: a retry after a failure part-way finds this request, not a new number
            issued[token] = {'id': rid, 'at': time.time()}
            _save_issued(issued)
        _ws('Requests').append_row([_cell(row.get(c, '')) for c in REQ_COLS], value_input_option='RAW')
        _ws('Lines').append_rows(_line_rows(rid, lines), value_input_option='RAW')
        refresh()
        return rid, False


def _cell(v):
    return v if isinstance(v, (int, float)) else str(v or '')


def _change(req_id, change):
    """Re-read one request's row under the file lock shared by every worker process, let change(row) edit it, and write
    it back. Returns the row, or None when nothing was written: the row is gone from the sheet, or change said no
    (returned False). Cells are read as stored, not as shown, so an amount column formatted as Currency keeps its
    number."""
    with FileLock(ID_LOCK), _lock:
        ws = _ws('Requests')
        col = ws.col_values(1)
        if req_id not in col:
            return None
        row = col.index(req_id) + 1
        cur = dict(zip(REQ_COLS, ['' if v is None else v for v in ws.row_values(row, value_render_option='UNFORMATTED_VALUE')] + [''] * len(REQ_COLS)))
        if change(cur) is False:
            return None
        for k in NUMERIC:
            cur[k] = _num(cur[k]) if cur[k] != '' else ''
        ws.update([[_cell(cur[c]) for c in REQ_COLS]], f'A{row}', value_input_option='RAW')
        refresh()
        return cur


def update(req_id, expect=None, **fields):
    """Change fields on one request (re-reads the row so concurrent edits to other columns survive; two changes at the
    same moment run one after the other). expect: the status it must still have, checked under the lock. Returns the
    row, or None when it is gone or its status is not `expect` (nothing is changed)."""
    return _change(req_id, lambda cur: False if expect and cur['status'] != expect else cur.update(fields))


def add_approval(req_id, role, name, day, note='', once=False):
    """Add one approval entry (fr_logic.add) to what is on the row now, so two approvals at the same moment both stay.
    once: skip it when the row already has an entry for this role (the 'ready' mark)."""
    def change(cur):
        text = str(cur.get('approvals') or '')
        if once and any(part.split('|')[0].strip() == role for part in text.split(';')):
            return False
        cur['approvals'] = fr_logic.add(text, role, name, day, note)
    return _change(req_id, change)


def close(req_id, status, expect=None, **fields):
    """Mark a request Paid or Rejected, or Submitted again (fields: paid_date, paid_ref, paid_amount, closed_by,
    closed_note). expect: the status it must have right now, so a double click or two people never close it twice.
    Returns None when it was not changed."""
    return update(req_id, expect=expect, status=status, **fields)


def delete(req_id, who):
    """Mark a request Deleted: it drops out of every list and its approval links stop working. The row stays, so its
    number is never given to another request (an old approval link must never approve a different one)."""
    return update(req_id, status='Deleted', closed_by=who)


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
