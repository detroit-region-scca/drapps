"""Reads and writes the Worker Points Ledger Google Sheet (LEDGER_SHEET_ID).

The sheet is the record. It stays usable by hand if this app is gone: balances are formulas, and every
change is one row on the Transactions tab. Tabs this module relies on:
  Ledger        A Member # | B Last | C First | D Life | E Opening balance (8/31/2026) |
                F Since (SUMIF of Transactions) | G Current balance | H Value ($5/pt)
  Transactions  Date | Member # | Name | Event code | Description | Points | Type | Source document
  Allocations   Role | Member # | Name | Pts/month Apr-Oct | Pts/month Nov-Mar
  Check         B1 total points, B2 value, B3 QBO balance, B4 as-of, B5 difference
  Reports       worker lists sent by event chairs (REPORT_COLS)
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
REQ_LOCK = os.path.join(DATA_DIR, 'wp_request_id.lock')


def enabled():
    return bool(os.environ.get('LEDGER_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _book():
    return open_book(os.environ['LEDGER_SHEET_ID'])


def _snapshot():
    """All four tabs in ONE read (Sheets allows 60 reads a minute), cached briefly."""
    def load():
        ranges = ['Ledger!A:H', 'Transactions!A:H', 'Allocations!A:E', 'Check!B1:B5']
        res = C.batch_get(os.environ['LEDGER_SHEET_ID'], ranges, params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        vr = res.get('valueRanges', [])
        return [r.get('values', []) for r in vr] + [[]] * (4 - len(vr))
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
            r = list(r) + [''] * (8 - len(r))
            mid = str(r[0]).strip()
            if not mid or mid == 'TOTAL':
                continue
            out.append({'member': mid, 'last': str(r[1]), 'first': str(r[2]), 'life': str(r[3]).upper() == 'LIFE',
                        'opening': _num(r[4]), 'since': _num(r[5]), 'balance': round(_num(r[6]), 3)})
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


def add_member(member, last, first, life=False):
    """Add a member at the bottom of the Ledger with no opening balance and the same formulas as every other row."""
    with _lock:
        ws = worksheet(os.environ['LEDGER_SHEET_ID'], 'Ledger')
        i = len(ws.col_values(1)) + 1
        ws.update([["'" + str(member), _text(last), _text(first), 'LIFE' if life else '', 0,
                    f'=SUMIF(Transactions!B:B,A{i},Transactions!F:F)', f'=E{i}+F{i}', f'=G{i}*5']],
                  f'A{i}', value_input_option='USER_ENTERED')
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
