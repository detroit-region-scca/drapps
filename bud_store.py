"""Reads and writes the Event Budgets Google Sheet (BUDGETS_SHEET_ID) in the DRSCCA Apps Shared Drive.

One row per event on Budgets (keyed by the #DR event code), one row per line on Lines with the three
columns of the region's budget form: last year actual, this year budget, this year actual. Original files
directors send go in a per-event folder under BUDGETS_FILES_FOLDER_ID.

The 'QBO Actuals' tab holds each event project's profit and loss by account, copied from QuickBooks by
tools/sync_qbo_actuals.py on the machine with the QuickBooks connection. The app only reads it.
"""
import os
import threading
import time
from datetime import date

import gspread
from filelock import FileLock

from storage import DATA_DIR, drive_folder, drive_upload, open_book, worksheet

BUD_COLS = ['event_code', 'event_name', 'program', 'date', 'chair', 'location', 'updated', 'updated_by', 'notes', 'files_url']
LINE_COLS = ['event_code', 'section', 'description', 'last_year', 'budget', 'actual', 'sort']
AMOUNTS = ('last_year', 'budget', 'actual')
CACHE_SECONDS = 15
# The Lines tab is rewritten whole on every save, so saves must not overlap across gunicorn workers.
SAVE_LOCK = os.path.join(DATA_DIR, 'budgets_save.lock')

_lock = threading.Lock()
_cache = {}


def enabled():
    return bool(os.environ.get('BUDGETS_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _ws(title):
    return worksheet(os.environ['BUDGETS_SHEET_ID'], title)


def refresh():
    _cache.clear()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _snapshot():
    hit = _cache.get('snap')
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    book = open_book(os.environ['BUDGETS_SHEET_ID'])
    try:
        res = book.values_batch_get(['Budgets!A:J', 'Lines!A:G', 'QBO Actuals!A:F'], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    except gspread.exceptions.APIError:
        # No QBO Actuals tab until the first sync has run.
        res = book.values_batch_get(['Budgets!A:J', 'Lines!A:G'], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    vr = [r.get('values', []) for r in res.get('valueRanges', [])] + [[], [], []]
    buds = [dict(zip(BUD_COLS, [str(v) for v in r] + [''] * len(BUD_COLS))) for r in vr[0][1:] if r and str(r[0]).strip()]
    lines = []
    for r in vr[1][1:]:
        if not r or not str(r[0]).strip():
            continue
        ln = dict(zip(LINE_COLS, list(r) + [''] * len(LINE_COLS)))
        for k in AMOUNTS:
            ln[k] = _num(ln[k])
        ln['event_code'], ln['description'], ln['section'] = str(ln['event_code']), str(ln['description']), str(ln['section'])
        ln['sort'] = _num(ln['sort']) or 0
        lines.append(ln)
    qbo = {}
    for r in vr[2][1:]:
        r = list(r) + [''] * 6
        if not str(r[0]).strip():
            continue
        q = qbo.setdefault(str(r[0]), {'project': str(r[1]), 'as_of': str(r[5]), 'lines': []})
        q['lines'].append({'section': str(r[2]), 'account': str(r[3]), 'amount': _num(r[4]) or 0.0})
    for q in qbo.values():
        inc = sum(ln['amount'] for ln in q['lines'] if ln['section'] == 'Income')
        exp = sum(ln['amount'] for ln in q['lines'] if ln['section'] == 'Expense')
        q['totals'] = {'income': round(inc, 2), 'expense': round(exp, 2), 'surplus': round(inc - exp, 2)}
    snap = (buds, lines, qbo)
    _cache['snap'] = (time.time(), snap)
    return snap


def qbo_actuals():
    """{event_code: {'project', 'as_of', 'lines': [{section, account, amount}], 'totals'}} from the last sync."""
    return _snapshot()[2]


def totals(lines):
    """Income, expense and surplus per column; a column with no amounts at all is None (not entered yet)."""
    out = {}
    for k in AMOUNTS:
        vals = [(ln['section'], ln[k]) for ln in lines if ln[k] is not None]
        if not vals:
            out[k] = None
            continue
        inc = sum(v for s, v in vals if s == 'Income')
        exp = sum(v for s, v in vals if s == 'Expense')
        out[k] = {'income': round(inc, 2), 'expense': round(exp, 2), 'surplus': round(inc - exp, 2)}
    return out


def budgets():
    """{event_code: header dict with lines and totals}"""
    buds, lines, _ = _snapshot()
    out = {}
    for b in buds:
        ls = sorted([ln for ln in lines if ln['event_code'] == b['event_code']], key=lambda ln: (ln['section'] != 'Income', ln['sort']))
        out[b['event_code']] = dict(b, lines=ls, totals=totals(ls))
    return out


def get(code):
    return budgets().get(code)


def _cell(v):
    if v is None:
        return ''
    return v if isinstance(v, (int, float)) else str(v)


def save(header, lines, files=()):
    """Replace one event's budget: header row upserted, its lines rewritten. Other events are untouched."""
    with FileLock(SAVE_LOCK), _lock:
        refresh()
        buds, all_lines, _ = _snapshot()
        code = header['event_code']
        old = next((b for b in buds if b['event_code'] == code), None)
        header = dict(header, updated=date.today().isoformat(), files_url=(old or {}).get('files_url', ''))
        files = [f for f in files if f and f.filename]
        if files:
            if not header['files_url']:
                header['files_url'] = drive_folder(f"{code} {header['event_name']}"[:120], os.environ['BUDGETS_FILES_FOLDER_ID'])['webViewLink']
            folder_id = header['files_url'].rstrip('/').split('/')[-1].split('?')[0]
            for f in files:
                drive_upload(f, folder_id)
        ws = _ws('Budgets')
        row = [_cell(header.get(c, '')) for c in BUD_COLS]
        if old:
            n = ws.col_values(1).index(code) + 1
            ws.update([row], f'A{n}', value_input_option='RAW')
        else:
            ws.append_row(row, value_input_option='RAW')
        keep = [ln for ln in all_lines if ln['event_code'] != code]
        new = [dict(ln, event_code=code, sort=i) for i, ln in enumerate(lines)]
        rows = [[_cell(ln.get(c)) for c in LINE_COLS] for ln in keep + new]
        lw = _ws('Lines')
        # Write first, then clear only what is left below, so a failure part-way never empties the tab.
        if len(rows) + 1 > lw.row_count:
            lw.add_rows(len(rows) + 1 - lw.row_count + 200)
        if rows:
            lw.update(rows, f'A2:G{len(rows) + 1}', value_input_option='RAW')
        if lw.row_count > len(rows) + 1:
            lw.batch_clear([f'A{len(rows) + 2}:G{lw.row_count}'])
        refresh()
