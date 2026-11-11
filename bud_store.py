"""Reads and writes the Event Budgets Google Sheet (BUDGETS_SHEET_ID) in the DRSCCA Apps Shared Drive.

One row per event on Budgets (keyed by the #DR event code), one row per line on Lines with the three
columns of the region's budget form: last year actual, this year budget, this year actual. Original files
directors send go in a per-event folder under BUDGETS_FILES_FOLDER_ID.

The 'QBO Actuals' tab holds each event project's profit and loss by account, copied from QuickBooks by
tools/qbo_nightly.py on the machine with the QuickBooks connection. The app only reads it.
"""
import hashlib
import os
import threading
from datetime import date

import gspread
from filelock import FileLock

from storage import DATA_DIR, drive_folder, drive_upload, worksheet
import sheet_cache as C

BUD_COLS = ['event_code', 'event_name', 'program', 'date', 'chair', 'location', 'updated', 'updated_by', 'notes', 'files_url',
            'approved', 'approved_by', 'last_year_event', 'draft', 'approved_hash', 'no_vote']
# approved_hash (added last, 2026-11): a fingerprint of the budget lines the board approved (lines_hash). A row
# approved before it existed has none, and status() falls back to comparing the days.
# no_vote ('YYYY-MM-DD by Name: why'): the admin took it off the board's list for good, e.g. a budget turned in after
# its season started. It counts as approved for spending.
LINE_COLS = ['event_code', 'section', 'description', 'last_year', 'budget', 'actual', 'sort']
AMOUNTS = ('last_year', 'budget', 'actual')
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
    C.invalidate(os.environ['BUDGETS_SHEET_ID'])


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


DATE_COLS = {'date', 'updated', 'approved'}


def _read_cell(col, v):
    """A Budgets cell as text. Someone editing the sheet by hand can turn a date into a real date cell, which the API
    sends as a day number (46277); give it back as YYYY-MM-DD like the app writes it."""
    if col in DATE_COLS and isinstance(v, (int, float)) and not isinstance(v, bool) and 20000 < v < 80000:
        from datetime import date, timedelta
        d = date(1899, 12, 30) + timedelta(days=int(v))
        if col == 'updated' and v % 1:
            from datetime import datetime
            return (datetime(1899, 12, 30) + timedelta(days=v)).isoformat(sep=' ', timespec='seconds')
        return d.isoformat()
    return str(v)


def _snapshot():
    hit, ver = _cache.get('snap'), C.version(os.environ['BUDGETS_SHEET_ID'])      # the version first: a write during the read below must not look fresh
    if hit and hit[0] == ver:
        return hit[1]
    # The whole Budgets tab, not A:O: a tab that does not have the newest column yet still reads.
    try:
        res = C.batch_get(os.environ['BUDGETS_SHEET_ID'], ["'Budgets'", 'Lines!A:G', 'QBO Actuals!A:F'], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    except gspread.exceptions.APIError:
        # No QBO Actuals tab until the first sync has run.
        res = C.batch_get(os.environ['BUDGETS_SHEET_ID'], ["'Budgets'", 'Lines!A:G'], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    vr = [r.get('values', []) for r in res.get('valueRanges', [])] + [[], [], []]
    buds = [dict(zip(BUD_COLS, [_read_cell(c, v) for c, v in zip(BUD_COLS, r)] + [''] * len(BUD_COLS))) for r in vr[0][1:] if r and str(r[0]).strip()]
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
    _cache['snap'] = (ver, snap)
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


def lines_hash(lines, text=('section', 'description'), amount='budget'):
    """A short fingerprint of the numbers the board approves: each budget line's section, description and budget
    amount (the Operations plan: text=('item', 'account'), amount='net'). Order does not matter, and lines with no
    amount (reported results added later) are left out, so entering results never changes it."""
    rows = sorted('|'.join([str(ln.get(k) or '').strip() for k in text] + [f'{float(ln[amount]):.2f}'])
                  for ln in lines if ln.get(amount) not in (None, ''))
    return hashlib.sha1('\n'.join(rows).encode()).hexdigest()[:12]


def changed_since_approved(row, now_hash):
    """Whether an approved budget (or the Operations plan) is no longer what the board approved. With the fingerprint
    taken at approval, any change to the numbers counts, even one the same day; a row approved before fingerprints
    were kept is compared by day, as it always was, so it stays approved until someone edits it."""
    if row.get('approved_hash'):
        return row['approved_hash'] != now_hash
    return str(row.get('updated', '')) > str(row['approved'])


def status(b):
    """The one answer to where a budget stands: 'draft' (pulled back, not on the board's list), 'approved', 'changed'
    (its numbers were edited after the board approved it), 'no_vote' (the admin said the board need not vote on it),
    'budget' (saved, waiting for the board) or None."""
    if not b:
        return None
    if b.get('draft'):
        return 'draft'
    if b.get('approved'):
        return 'changed' if changed_since_approved(b, lines_hash(b.get('lines') or [])) else 'approved'
    if b.get('no_vote'):
        return 'no_vote'
    return 'budget'


def _cell(v):
    if v is None:
        return ''
    return v if isinstance(v, (int, float)) else str(v)


def _heading(ws):
    """Columns added later get their heading the first time a budget is saved."""
    if not _cache.get('heading_ok'):
        if ws.col_count < len(BUD_COLS):          # the tab must be wide enough first, or the write is refused
            ws.add_cols(len(BUD_COLS) - ws.col_count)
        top = ws.row_values(1)
        if len(top) < len(BUD_COLS):
            ws.update([BUD_COLS[len(top):]], f'{chr(ord("A") + len(top))}1', value_input_option='RAW')
        _cache['heading_ok'] = True


def history(code, years=6):
    """This event and the earlier ones its budgets point back to ("Last year's event"), newest first:
    [{'code', 'b'}]. Only links a person chose; stops at a gap or a loop."""
    buds, out, seen = budgets(), [], set()
    while code and code not in seen and len(out) < years:
        seen.add(code)
        b = buds.get(code)
        out.append({'code': code, 'b': b})
        code = (b or {}).get('last_year_event', '')
    return out


def _same_budget(old, header, old_lines, new_lines):
    """True when the header and lines are what was saved before (only last_year_event may differ). Entering actuals
    goes through save(actuals_only=True) and never counts as a change either."""
    keys = ('chair', 'location', 'notes', 'event_name', 'program', 'date')
    if any(str(old.get(k) or '') != str(header.get(k) or '') for k in keys):
        return False
    pick = lambda ls: [(ln['section'], ln['description'], *(ln.get(k) for k in AMOUNTS)) for ln in ls]  # noqa: E731
    return pick(sorted(old_lines, key=lambda ln: ln['sort'])) == pick(new_lines)


def save(header, lines, files=(), actuals_only=False):
    """Replace one event's budget: header row upserted, its lines rewritten. Other events are untouched."""
    with FileLock(SAVE_LOCK), _lock:
        refresh()
        buds, all_lines, _ = _snapshot()
        code = header['event_code']
        old = next((b for b in buds if b['event_code'] == code), None)
        files = [f for f in files if f and f.filename]
        # Changing only "Last year's event" is not a change to the budget: an approved one stays approved.
        same = old and not files and (actuals_only or _same_budget(old, header, [ln for ln in all_lines if ln['event_code'] == code], lines))
        header = dict(header, updated=old['updated'] if same else date.today().isoformat(), files_url=(old or {}).get('files_url', ''),
                      approved=(old or {}).get('approved', ''), approved_by=(old or {}).get('approved_by', ''),
                      draft=header['draft'] if 'draft' in header else (old or {}).get('draft', ''),
                      approved_hash=(old or {}).get('approved_hash', ''), no_vote=(old or {}).get('no_vote', ''))
        if old and old.get('approved') and not old.get('approved_hash') and not changed_since_approved(old, ''):
            # Approved before fingerprints were kept and not edited since: what is saved now is what the board
            # approved, so fingerprint that. From here on a change shows as "changed" even on the approval day.
            header['approved_hash'] = lines_hash([ln for ln in all_lines if ln['event_code'] == code])
        if files:
            if not header['files_url']:
                header['files_url'] = drive_folder(f"{code} {header['event_name']}"[:120], os.environ['BUDGETS_FILES_FOLDER_ID'])['webViewLink']
            folder_id = header['files_url'].rstrip('/').split('/')[-1].split('?')[0]
            for f in files:
                drive_upload(f, folder_id)
        ws = _ws('Budgets')
        _heading(ws)
        row = [_cell(header.get(c, '')) for c in BUD_COLS]
        col = ws.col_values(1)
        if code in col:
            ws.update([row], f'A{col.index(code) + 1}', value_input_option='RAW')
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


def delete(code):
    """Remove one budget: its Budgets row and its Lines. Its files folder stays in Drive."""
    with FileLock(SAVE_LOCK), _lock:
        refresh()
        buds, all_lines, _ = _snapshot()
        if not any(b['event_code'] == code for b in buds):
            return False
        keep = [ln for ln in all_lines if ln['event_code'] != code]
        rows = [[_cell(ln.get(c)) for c in LINE_COLS] for ln in keep]
        lw = _ws('Lines')
        if rows:
            lw.update(rows, f'A2:G{len(rows) + 1}', value_input_option='RAW')
        if lw.row_count > len(rows) + 1:
            lw.batch_clear([f'A{len(rows) + 2}:G{lw.row_count}'])
        ws = _ws('Budgets')
        col = ws.col_values(1)
        if code in col:
            ws.delete_rows(col.index(code) + 1)
        refresh()
        return True


def set_approved(codes, approved, who):
    """Record (or with approved='' clear) the date the board approved these events' budgets, with the fingerprint of
    the lines as they are right now (lines_hash): that is what "changed since approved" is judged against. A budget
    edited after the meeting date being recorded gets no fingerprint, so it shows as changed (the day comparison)."""
    codes = [codes] if isinstance(codes, str) else list(codes)
    with FileLock(SAVE_LOCK), _lock:
        refresh()
        buds, lines, _ = _snapshot()
        edited = {b['event_code']: str(b.get('updated', '')) for b in buds}
        ws = _ws('Budgets')
        _heading(ws)
        col = ws.col_values(1)
        upd = []
        for c in codes:
            if c in col:
                n = col.index(c) + 1
                upd += [{'range': f'K{n}', 'values': [[approved, who if approved else '']]},
                        {'range': f'O{n}', 'values': [[lines_hash([ln for ln in lines if ln['event_code'] == c])
                                                        if approved and edited.get(c, '') <= str(approved) else '']]}]
        if upd:
            ws.batch_update(upd, value_input_option='RAW')
        refresh()


def set_no_vote(code, note):
    """Mark one budget as needing no board vote (note = 'YYYY-MM-DD by Name: why'), or with note='' put it back on the
    board's list."""
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws('Budgets')
        _heading(ws)
        col = ws.col_values(1)
        if code not in col:
            raise ValueError('That budget has not been saved yet.')
        ws.batch_update([{'range': f'P{col.index(code) + 1}', 'values': [[note]]}], value_input_option='RAW')
        refresh()


def set_draft(code, draft):
    """Put one budget back into draft (draft = 'YYYY-MM-DD by Name'), which also clears any board approval, or with
    draft='' mark it ready for the board again."""
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws('Budgets')
        _heading(ws)
        col = ws.col_values(1)
        if code not in col:
            raise ValueError('That budget has not been saved yet.')
        n = col.index(code) + 1
        upd = [{'range': f'N{n}', 'values': [[draft]]}]
        if draft:
            upd += [{'range': f'K{n}:L{n}', 'values': [['', '']]}, {'range': f'O{n}', 'values': [['']]}]
        ws.batch_update(upd, value_input_option='RAW')
        refresh()
