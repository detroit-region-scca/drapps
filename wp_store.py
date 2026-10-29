"""Reads and writes the Worker Points Ledger Google Sheet (LEDGER_SHEET_ID).

The sheet is the record. It stays usable by hand if this app is gone: balances are formulas, and every
change is one row on the Transactions tab. Tabs this module relies on:
  Ledger        A Member # | B Last | C First | D Life | E Membership exp | F Balance 12/31/2025 |
                G Used 2026 (thru 8/31) | H Earned 2026 (thru 8/31) | I Balance at 8/31 (formula) |
                J Since 8/31 (SUMIF of Transactions) | K Current balance | L Value; last row = TOTAL
  Transactions  Date | Member # | Name | Event code | Description | Points | Type | Source document
  Allocations   Role | Member # | Name | Pts/month Apr-Oct | Pts/month Nov-Mar
  Check         B1 total points, B2 value, B3 QBO balance, B4 as-of, B5 difference
"""
import os
import threading
import time
from datetime import date, timedelta

from storage import open_book, worksheet

CACHE_SECONDS = 15
_lock = threading.Lock()
_cache = {}
EPOCH = date(1899, 12, 30)  # Sheets serial day 0


def enabled():
    return bool(os.environ.get('LEDGER_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _book():
    return open_book(os.environ['LEDGER_SHEET_ID'])


def _snapshot():
    """All four tabs in ONE read (Sheets allows 60 reads a minute), cached briefly."""
    def load():
        res = _book().values_batch_get(['Ledger!A:L', 'Transactions!A:H', 'Allocations!A:E', 'Check!B1:B5'],
                                       params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        vr = res.get('valueRanges', [])
        return [r.get('values', []) for r in vr] + [[]] * (4 - len(vr))
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


def append_transactions(rows):
    """rows: dicts with date (date), member, name, event_code, description, points, type, source."""
    values = [[t['date'].isoformat(), "'" + t['member'], t['name'], t['event_code'], t['description'], t['points'], t['type'], t['source']]
              for t in rows]
    with _lock:
        worksheet(os.environ['LEDGER_SHEET_ID'], 'Transactions').append_rows(values, value_input_option='USER_ENTERED')
        _invalidate()


def add_member(member, last, first, exp, life=False):
    """Insert a member just above the TOTAL row, with the same formulas as every other row."""
    with _lock:
        ws = worksheet(os.environ['LEDGER_SHEET_ID'], 'Ledger')
        col_a = ws.col_values(1)
        total_row = col_a.index('TOTAL') + 1
        i = total_row
        ws.insert_row(["'" + member, last, first, 'LIFE' if life else '', "'" + exp, 0, 0, 0,
                       f'=F{i}-G{i}+H{i}', f'=SUMIF(Transactions!B:B,A{i},Transactions!F:F)', f'=I{i}+J{i}', f'=K{i}*5'],
                      index=i, value_input_option='USER_ENTERED')
        _invalidate()


def set_qbo_balance(amount, as_of):
    with _lock:
        ws = worksheet(os.environ['LEDGER_SHEET_ID'], 'Check')
        ws.update([[amount], [as_of.isoformat()]], 'B3:B4', value_input_option='USER_ENTERED')
        _invalidate()
