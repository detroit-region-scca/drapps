"""Reads and writes the Worker Points Ledger Google Sheet (LEDGER_SHEET_ID).

The sheet is the record. It stays usable by hand if this app is gone: balances are formulas, and every
change is one row on the Transactions tab. Tabs this module relies on:
  Ledger        A Member # | B Last | C First | D Life | E Membership exp | F Balance 12/31/2025 |
                G Used 2026 (thru 8/31) | H Earned 2026 (thru 8/31) | I Balance at 8/31 (formula) |
                J Since 8/31 (SUMIF of Transactions) | K Current balance | L Value; last row = TOTAL
  Transactions  Date | Member # | Name | Event code | Description | Points | Type | Source document
  Allocations   Role | Member # | Name | Pts/month Apr-Oct | Pts/month Nov-Mar
  Check         B1 total points, B2 value, B3 QBO balance, B4 as-of, B5 difference
  Requests      redemption requests (REQ_COLS); approving one writes a Used row to Transactions
"""
import os
import threading
import time
from datetime import date, timedelta

import gspread
from filelock import FileLock

from storage import DATA_DIR, open_book, worksheet

CACHE_SECONDS = 15
_lock = threading.Lock()
_cache = {}
EPOCH = date(1899, 12, 30)  # Sheets serial day 0
REQ_COLS = ['id', 'requested', 'status', 'member', 'name', 'points', 'amount', 'for', 'payee', 'payee_address', 'requested_by',
            'requested_email', 'notes', 'decided', 'decided_by', 'funds_request', 'decision_note']
REQ_LOCK = os.path.join(DATA_DIR, 'wp_request_id.lock')


def enabled():
    return bool(os.environ.get('LEDGER_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _book():
    return open_book(os.environ['LEDGER_SHEET_ID'])


def _snapshot():
    """All four tabs in ONE read (Sheets allows 60 reads a minute), cached briefly."""
    def load():
        ranges = ['Ledger!A:L', 'Transactions!A:H', 'Allocations!A:E', 'Check!B1:B5', 'Requests!A:Q']
        try:
            res = _book().values_batch_get(ranges, params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        except gspread.exceptions.APIError:
            # A ledger without the Requests tab (added 2026-10) still works; redemption requests just show none.
            res = _book().values_batch_get(ranges[:4], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        vr = res.get('valueRanges', [])
        return [r.get('values', []) for r in vr] + [[]] * (5 - len(vr))
    return _cached('snapshot', load)


def _cached(key, loader):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    val = loader()
    _cache[key] = (time.time(), val)
    return val


def _invalidate():
    _cache.clear()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _as_date(v):
    if isinstance(v, (int, float)) and v > 0:
        return EPOCH + timedelta(days=int(v))
    if isinstance(v, str) and len(v) >= 10:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def members():
    def load():
        rows = _snapshot()[0]
        out = []
        for r in rows[1:]:
            r = list(r) + [''] * (12 - len(r))
            mid = str(r[0]).strip()
            if not mid or mid == 'TOTAL':
                continue
            exp = str(r[4]).strip()
            out.append({'member': mid, 'last': str(r[1]), 'first': str(r[2]), 'life': str(r[3]).upper() == 'LIFE',
                        'exp': exp, 'exp_sort': (exp.split('/')[1] + exp.split('/')[0].zfill(2)) if '/' in exp else '',
                        'opening': _num(r[8]), 'since': _num(r[9]), 'balance': round(_num(r[10]), 3)})
        return out
    return _cached('members', load)


def transactions():
    def load():
        rows = _snapshot()[1]
        out = []
        for r in rows[1:]:
            r = list(r) + [''] * (8 - len(r))
            if not str(r[1]).strip():
                continue
            out.append({'date': _as_date(r[0]), 'member': str(r[1]).strip(), 'name': str(r[2]), 'event_code': str(r[3]),
                        'description': str(r[4]), 'points': _num(r[5]), 'type': str(r[6]), 'source': str(r[7])})
        return out
    return _cached('transactions', load)


def allocations():
    def load():
        rows = _snapshot()[2]
        return [{'role': str(r[0]), 'member': str(r[1]).strip(), 'name': str(r[2]), 'season': _num(r[3]), 'offseason': _num(r[4])}
                for r in rows[1:] if len(r) >= 5 and str(r[1]).strip()]
    return _cached('allocations', load)


def check():
    def load():
        vals = _snapshot()[3]
        v = [(row[0] if row else '') for row in vals] + [''] * 5
        return {'points': _num(v[0]), 'value': _num(v[1]), 'qbo': _num(v[2]), 'as_of': _as_date(v[3]), 'difference': _num(v[4])}
    return _cached('check', load)


def requests():
    def load():
        out = []
        for r in _snapshot()[4][1:]:
            if not r or not str(r[0]).strip():
                continue
            d = dict(zip(REQ_COLS, [str(v) for v in r] + [''] * len(REQ_COLS)))
            d['points'], d['amount'] = _num(d['points']), _num(d['amount'])
            out.append(d)
        return out
    return _cached('requests', load)


def add_request(req):
    """req: dict of REQ_COLS fields without id/requested/status. Returns the new id (PR-YYYY-NNN)."""
    with FileLock(REQ_LOCK), _lock:
        ws = worksheet(os.environ['LEDGER_SHEET_ID'], 'Requests')
        prefix = f'PR-{date.today().year}-'
        nums = [int(v[len(prefix):]) for v in ws.col_values(1) if v.startswith(prefix) and v[len(prefix):].isdigit()]
        rid = f'{prefix}{max(nums, default=0) + 1:03d}'
        row = dict(req, id=rid, requested=date.today().isoformat(), status='Pending')
        ws.append_row([_req_cell(row, c) for c in REQ_COLS], value_input_option='RAW')
        _invalidate()
        return rid


def _req_cell(row, c):
    v = row.get(c, '')
    return _num(v) if c in ('points', 'amount') and v != '' else str(v or '')


def decide_request(rid, status, **fields):
    """Close a request: status Approved or Declined, with decided/decided_by/funds_request/decision_note."""
    with _lock:
        ws = worksheet(os.environ['LEDGER_SHEET_ID'], 'Requests')
        row = ws.col_values(1).index(rid) + 1
        cur = dict(zip(REQ_COLS, ws.row_values(row) + [''] * len(REQ_COLS)))
        cur.update(fields, status=status)
        ws.update([[_req_cell(cur, c) for c in REQ_COLS]], f'A{row}', value_input_option='RAW')
        _invalidate()


def refresh():
    """Drop the cached snapshot. Every write path calls this first: gunicorn runs several workers,
    and a balance or "already posted" check made from another worker's stale copy would be wrong."""
    _invalidate()


def _text(v):
    """A typed value stored as plain text, never as a formula (=, +, - or @ at the start would
    otherwise be evaluated by Sheets when written with USER_ENTERED)."""
    v = str(v or '')
    return "'" + v if v[:1] in ('=', '+', '-', '@') else v


def append_transactions(rows):
    """rows: dicts with date (date), member, name, event_code, description, points, type, source.

    Written RAW: text stays text (no formula can be injected), the member number stays text so the
    Ledger's SUMIF matches it, the date is stored as YYYY-MM-DD and points as a number."""
    values = [[t['date'].isoformat(), str(t['member']), str(t['name']), str(t['event_code']), str(t['description']),
               float(t['points']), str(t['type']), str(t['source'])] for t in rows]
    with _lock:
        worksheet(os.environ['LEDGER_SHEET_ID'], 'Transactions').append_rows(values, value_input_option='RAW')
        _invalidate()


def add_member(member, last, first, exp, life=False):
    """Insert a member just above the TOTAL row, with the same formulas as every other row."""
    with _lock:
        ws = worksheet(os.environ['LEDGER_SHEET_ID'], 'Ledger')
        col_a = ws.col_values(1)
        total_row = col_a.index('TOTAL') + 1
        i = total_row
        ws.insert_row(["'" + str(member), _text(last), _text(first), 'LIFE' if life else '', "'" + str(exp), 0, 0, 0,
                       f'=F{i}-G{i}+H{i}', f'=SUMIF(Transactions!B:B,A{i},Transactions!F:F)', f'=I{i}+J{i}', f'=K{i}*5'],
                      index=i, value_input_option='USER_ENTERED')
        _invalidate()


def set_qbo_balance(amount, as_of):
    with _lock:
        ws = worksheet(os.environ['LEDGER_SHEET_ID'], 'Check')
        ws.update([[amount], [as_of.isoformat()]], 'B3:B4', value_input_option='USER_ENTERED')
        _invalidate()
