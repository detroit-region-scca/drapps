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
import sheet_cache as C
import wp_logic as L

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
            res = C.batch_get(os.environ['LEDGER_SHEET_ID'], ranges, params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        except gspread.exceptions.APIError:
            # A ledger without the Requests tab (added 2026-10) still works; redemption requests just show none.
            res = C.batch_get(os.environ['LEDGER_SHEET_ID'], ranges[:4], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        vr = res.get('valueRanges', [])
        return [r.get('values', []) for r in vr] + [[]] * (5 - len(vr))
    return _cached('snapshot', load)


def _cached(key, loader):
    hit = _cache.get(key)
    if hit and hit[0] == C.version(os.environ['LEDGER_SHEET_ID']):
        return hit[1]
    val = loader()
    _cache[key] = (C.version(os.environ['LEDGER_SHEET_ID']), val)
    return val


def _invalidate():
    _cache.clear()
    C.invalidate(os.environ['LEDGER_SHEET_ID'])


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
    publish_standings_soon()


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
    publish_standings_soon()


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


# ---------- public standings (names and points) for drscca.org, in the Region drive's public folder ----------

def publish_standings():
    """Rewrite the public Worker Points Standings sheet from the ledger. Returns how many members it lists."""
    sid = os.environ.get('WP_STANDINGS_SHEET_ID')
    if not sid:
        return None
    from datetime import date as _date
    _invalidate()
    out = L.public_standings(members(), transactions())
    ws = worksheet(sid, 'Standings')
    as_of = out['as_of'].isoformat() if out['as_of'] else ''
    values = [['Name', 'Points', 'Value', 'Points recorded through', 'Published']]
    values += [[r['name'], r['points'], round(r['points'] * L.POINT_VALUE, 2), as_of if i == 0 else '', _date.today().isoformat() if i == 0 else '']
               for i, r in enumerate(out['members'])]
    ws.batch_clear(['A:E'])
    ws.update(values, 'A1', value_input_option='RAW')
    return len(out['members'])


def publish_standings_soon():
    """Publish in the background after a ledger change, so the page that made the change stays fast."""
    import threading
    import logging

    def run():
        try:
            publish_standings()
        except Exception as e:      # the website copy is a convenience; never fail a ledger write over it
            logging.getLogger(__name__).error(f'publishing worker points standings failed: {e}')
    threading.Thread(target=run, daemon=True).start()


# ---------- worker reports from event chairs (tab "Reports" in the ledger; private) ----------

REPORT_COLS = ['id', 'submitted', 'status', 'event_code', 'event_name', 'date', 'specialty', 'workers', 'notes',
               'submitted_by', 'email', 'decided', 'decided_by']


def _reports_ws():
    book_id = os.environ['LEDGER_SHEET_ID']
    from storage import open_book
    try:
        return worksheet(book_id, 'Reports')
    except Exception:
        ws = open_book(book_id).add_worksheet('Reports', rows=200, cols=len(REPORT_COLS))
        ws.update([REPORT_COLS], 'A1', value_input_option='RAW')
        from storage import _tabs
        _tabs[(book_id, 'Reports')] = ws
        return ws


def reports():
    vals = _reports_ws().get_all_values()
    return [dict(zip(REPORT_COLS, list(r) + [''] * len(REPORT_COLS))) for r in vals[1:] if r and r[0]]


def add_report(rep):
    """rep: REPORT_COLS fields without id/submitted/status. Returns the new id (WR-YYYY-NNN)."""
    from datetime import date as _date
    with _lock:
        ws = _reports_ws()
        year = _date.today().year
        n = sum(1 for r in ws.col_values(1)[1:] if r.startswith(f'WR-{year}-'))
        rid = f'WR-{year}-{n + 1:03d}'
        row = dict(rep, id=rid, submitted=_date.today().isoformat(), status='Submitted')
        ws.append_row([_text(row.get(c, '')) for c in REPORT_COLS], value_input_option='RAW')
    return rid


def close_report(rid, status, who):
    from datetime import date as _date
    with _lock:
        ws = _reports_ws()
        row = ws.col_values(1).index(rid) + 1
        ws.update([[status]], f'C{row}', value_input_option='RAW')
        ws.update([[_date.today().isoformat(), who]], f'L{row}:M{row}', value_input_option='RAW')
