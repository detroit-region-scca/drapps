"""Reads and writes the DRSCCA Annual Plan Google Sheet (PLAN_SHEET_ID) in the DRSCCA Apps Shared Drive.

  Plan            one row per planned event or item: year, program, item, month, net (+ profit, - cost)
  Programs        one row per program and year: director, email, notes for the board, last update
  QBO Programs    the region profit and loss by account and year      } written nightly by
  QBO Status      bank balances and the worker points reserve          } tools/qbo_nightly.py,
  QBO Exceptions  things in the books the treasurer should look at     } read only here
  Reviewed        exception keys the treasurer has looked at, so they stay hidden
"""
import os
import threading
import time
from datetime import date

import gspread
from filelock import FileLock

import qbo_logic
from storage import DATA_DIR, open_book, worksheet

PLAN_COLS = ['year', 'program', 'item', 'month', 'net', 'event_code', 'note', 'sort']
PROG_COLS = ['year', 'program', 'director', 'email', 'notes', 'updated', 'updated_by']
EXC_COLS = ['key', 'kind', 'date', 'name', 'amount', 'detail', 'link', 'as_of']
CACHE_SECONDS = 15
SAVE_LOCK = os.path.join(DATA_DIR, 'plan_save.lock')
RANGES = ['Plan!A:H', 'Programs!A:G', 'QBO Programs!A:F', 'QBO Status!A:C', 'QBO Exceptions!A:H', 'Reviewed!A:D']

_lock = threading.Lock()
_cache = {}


def enabled():
    return bool(os.environ.get('PLAN_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _ws(title):
    return worksheet(os.environ['PLAN_SHEET_ID'], title)


def refresh():
    _cache.clear()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows(values, cols):
    return [dict(zip(cols, list(r) + [''] * len(cols))) for r in values[1:] if r and str(r[0]).strip()]


def _snapshot():
    hit = _cache.get('snap')
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    res = open_book(os.environ['PLAN_SHEET_ID']).values_batch_get(RANGES, params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    vr = [r.get('values', []) for r in res.get('valueRanges', [])]
    lines = _rows(vr[0], PLAN_COLS)
    for ln in lines:
        ln['year'], ln['program'], ln['item'], ln['note'] = str(ln['year']), str(ln['program']), str(ln['item']), str(ln['note'])
        ln['net'], ln['sort'] = _num(ln['net']), _num(ln['sort']) or 0
        ln['month'] = int(ln['month']) if _num(ln['month']) else None
        ln['event_code'] = str(ln['event_code'])
    progs = _rows(vr[1], PROG_COLS)
    for p in progs:
        p.update({k: str(p[k]) for k in PROG_COLS})
    qbo = _rows(vr[2], ['year', 'program', 'section', 'account', 'amount', 'as_of'])
    status = {str(r['item']): {'amount': _num(r['amount']), 'as_of': str(r['as_of'])} for r in _rows(vr[3], ['item', 'amount', 'as_of'])}
    exc = _rows(vr[4], EXC_COLS)
    for e in exc:
        e.update({k: str(e[k]) for k in EXC_COLS if k != 'amount'}, amount=_num(e['amount']))
    reviewed = {str(r['key']): r for r in _rows(vr[5], ['key', 'reviewed', 'by', 'note'])}
    snap = (lines, progs, qbo, status, exc, reviewed)
    _cache['snap'] = (time.time(), snap)
    return snap


def years():
    lines, progs, qbo = _snapshot()[:3]
    ys = {str(x['year']) for x in lines + progs + qbo} | {str(date.today().year), str(date.today().year + 1)}
    return sorted(ys, reverse=True)


def plan(year):
    """{program: {'lines': [...], 'net', 'info': Programs row or {}}} for one year."""
    lines, progs = _snapshot()[:2]
    out = {}
    for ln in sorted((x for x in lines if x['year'] == str(year)), key=lambda x: x['sort']):
        out.setdefault(ln['program'], {'lines': []})['lines'].append(ln)
    for p in out.values():
        p['net'] = round(sum(ln['net'] or 0 for ln in p['lines']), 2)
    for info in progs:
        if info['year'] == str(year):
            out.setdefault(info['program'], {'lines': [], 'net': 0.0})['info'] = info
    for p in out.values():
        p.setdefault('info', {})
    return out


def actuals(year):
    """{program: {'income', 'expense', 'net', 'lines'}} from QuickBooks for one year, plus 'as_of'."""
    rows = [r for r in _snapshot()[2] if str(r['year']) == str(year)]
    lines = [{'section': str(r['section']), 'account': str(r['account']), 'amount': _num(r['amount']) or 0.0} for r in rows]
    return qbo_logic.program_totals(lines), (str(rows[0]['as_of']) if rows else '')


def status():
    """{'Checking': {'amount', 'as_of'}, ...}: bank balances and the worker points reserve as of the last sync."""
    return _snapshot()[3]


def exceptions(include_reviewed=False):
    exc, reviewed = _snapshot()[4], _snapshot()[5]
    out = [dict(e, reviewed=reviewed.get(e['key'])) for e in exc]
    return out if include_reviewed else [e for e in out if not e['reviewed']]


def mark_reviewed(key, who, note=''):
    with _lock:
        _ws('Reviewed').append_row([key, date.today().isoformat(), who, note], value_input_option='RAW')
        refresh()


def _cell(v):
    if v is None:
        return ''
    return v if isinstance(v, (int, float)) else str(v)


def save(year, program, info, lines):
    """Replace one program's plan for one year: its Programs row upserted, its Plan rows rewritten."""
    year = str(year)
    with FileLock(SAVE_LOCK), _lock:
        refresh()
        all_lines, progs = _snapshot()[:2]
        info = dict(info, year=year, program=program, updated=date.today().isoformat())
        pw = _ws('Programs')
        row = [_cell(info.get(c, '')) for c in PROG_COLS]
        at = next((i for i, p in enumerate(progs) if p['year'] == year and p['program'] == program), None)
        if at is None:
            pw.append_row(row, value_input_option='RAW')
        else:
            pw.update([row], f'A{at + 2}', value_input_option='RAW')
        keep = [ln for ln in all_lines if not (ln['year'] == year and ln['program'] == program)]
        new = [dict(ln, year=year, program=program, sort=i) for i, ln in enumerate(lines)]
        rows = [[_cell(ln.get(c)) for c in PLAN_COLS] for ln in keep + new]
        lw = _ws('Plan')
        # Write first, then clear only what is left below, so a failure part-way never empties the tab.
        if len(rows) + 1 > lw.row_count:
            lw.add_rows(len(rows) + 1 - lw.row_count + 200)
        if rows:
            lw.update(rows, f'A2:H{len(rows) + 1}', value_input_option='RAW')
        if lw.row_count > len(rows) + 1:
            lw.batch_clear([f'A{len(rows) + 2}:H{lw.row_count}'])
        refresh()


def write_tab(title, cols, rows):
    """Replace a whole QBO mirror tab (used by the nightly sync)."""
    try:
        ws = _ws(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = open_book(os.environ['PLAN_SHEET_ID']).add_worksheet(title, rows=len(rows) + 100, cols=len(cols))
    if len(rows) + 1 > ws.row_count:
        ws.add_rows(len(rows) + 1 - ws.row_count + 100)
    ws.update([cols] + [[_cell(v) for v in r] for r in rows], 'A1', value_input_option='RAW')
    if ws.row_count > len(rows) + 1:
        ws.batch_clear([f'A{len(rows) + 2}:{chr(64 + len(cols))}{ws.row_count}'])
