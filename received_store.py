"""Money received: cash and checks taken in for the region (gate cash, merchandise, sponsor checks, donations).

Lives in the Funds Requests sheet beside the requests, on two tabs made the first time they are needed: one row
per report on "Money received" and one row per line on "Received lines" (joined by id). Photos of the count sheet
or the checks go in a Drive folder per report under FUNDS_RECEIPTS_FOLDER_ID.

A report is Received until the money is in the bank, then Deposited: the nightly QuickBooks sync matches it to a
deposit (match_deposits), or the treasurer marks it by hand.
"""
import os
import re
import threading
from datetime import date

from filelock import FileLock

import qbo_logic
from storage import DATA_DIR, drive_folder, drive_upload, open_book, worksheet
import sheet_cache as C

TAB, LINES_TAB = 'Money received', 'Received lines'
COLS = ['id', 'received', 'reported', 'status', 'event_code', 'event_name', 'program', 'reported_by', 'reported_email',
        'cash', 'checks', 'total', 'counted_by', 'second_counter', 'held_by', 'notes', 'files_url',
        'deposit_date', 'deposit_ref', 'deposit_amount', 'closed_by']
LINE_COLS = ['report_id', 'description', 'category', 'form', 'check_number', 'amount']
NUMERIC = ('cash', 'checks', 'total', 'deposit_amount')
CATEGORIES = ['Entry fees', 'Merchandise', 'Food and drink', 'Sponsorship', 'Donation', 'Other']
FORMS = ['Cash', 'Check']
ID_LOCK = os.path.join(DATA_DIR, 'received_id.lock')

_lock = threading.Lock()
_cache = {}


def enabled():
    return bool(os.environ.get('FUNDS_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _sid():
    return os.environ['FUNDS_SHEET_ID']


def _ws(title):
    return worksheet(_sid(), title)


def _ensure_tabs():
    """Make the two tabs, with their headings, if the sheet does not have them yet."""
    book = open_book(_sid())
    have = {w.title for w in book.worksheets()}
    for title, cols in ((TAB, COLS), (LINES_TAB, LINE_COLS)):
        if title not in have:
            book.add_worksheet(title, rows=200, cols=len(cols)).update([cols], 'A1', value_input_option='RAW')


def refresh():
    _cache.clear()
    C.invalidate(_sid())


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def _snapshot():
    hit, ver = _cache.get('snap'), C.version(_sid())      # the version first: a write during the read below must not look fresh
    if hit and hit[0] == ver:
        return hit[1]
    try:
        res = C.batch_get(_sid(), [f"'{TAB}'!A:U", f"'{LINES_TAB}'!A:F"], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    except Exception:
        return [], []        # the tabs are made on the first report
    vr = [r.get('values', []) for r in res.get('valueRanges', [])] + [[], []]
    reps = [dict(zip(COLS, [str(v) for v in r] + [''] * len(COLS))) for r in vr[0][1:] if r and str(r[0]).strip()]
    lines = [dict(zip(LINE_COLS, [str(v) for v in r] + [''] * len(LINE_COLS))) for r in vr[1][1:] if r and str(r[0]).strip()]
    for r in reps:
        for k in NUMERIC:
            r[k] = _num(r[k])
    for ln in lines:
        ln['amount'] = _num(ln['amount'])
    snap = (reps, lines)
    _cache['snap'] = (ver, snap)
    return snap


def reports():
    """Every report except deleted ones."""
    return [r for r in _snapshot()[0] if r['status'] != 'Deleted']


def get(rid):
    reps, lines = _snapshot()
    r = next((r for r in reps if r['id'] == rid and r['status'] != 'Deleted'), None)
    return dict(r, lines=[ln for ln in lines if ln['report_id'] == rid]) if r else None


def totals(lines):
    """(cash, checks, total) for a report's lines."""
    cash = round(sum(ln['amount'] for ln in lines if ln['form'] == 'Cash'), 2)
    checks = round(sum(ln['amount'] for ln in lines if ln['form'] == 'Check'), 2)
    return cash, checks, round(cash + checks, 2)


def single_count(r):
    """Cash counted by one person only: worth a second look."""
    return bool(r.get('cash')) and not (r.get('second_counter') or '').strip()


def _next_id(year):
    prefix = f'CR-{year}-'
    nums = [int(v[len(prefix):]) for v in _ws(TAB).col_values(1) if v.startswith(prefix) and v[len(prefix):].isdigit()]
    return f'{prefix}{max(nums, default=0) + 1:03d}'


def _cell(v):
    return v if isinstance(v, (int, float)) else str(v or '')


def create(rep, lines, files):
    """rep: COLS fields without id/reported/status/totals/files_url. Returns the new id."""
    with FileLock(ID_LOCK), _lock:
        _ensure_tabs()
        rid = _next_id(date.today().year)
        files = [f for f in files if f and f.filename]
        url = ''
        if files:
            folder = drive_folder(f"{rid} - {rep.get('event_name') or rep.get('program', '')}"[:120], os.environ['FUNDS_RECEIPTS_FOLDER_ID'])
            for f in files:
                drive_upload(f, folder['id'])
            url = folder['webViewLink']
        cash, checks, total = totals(lines)
        row = dict(rep, id=rid, reported=date.today().isoformat(), status='Received', cash=cash, checks=checks, total=total, files_url=url)
        _ws(TAB).append_row([_cell(row.get(c, '')) for c in COLS], value_input_option='RAW')
        _ws(LINES_TAB).append_rows([[rid] + [_cell(ln.get(c, '')) for c in LINE_COLS[1:]] for ln in lines], value_input_option='RAW')
        refresh()
        return rid


def update(rid, expect=None, **fields):
    """Change fields on one report. expect: the status it must still have, checked under the lock. Returns the row, or
    None when the row is gone from the sheet or its status is not `expect` (nothing is changed). Cells are read as
    stored, not as shown, so an amount column formatted as Currency keeps its number."""
    with FileLock(ID_LOCK), _lock:
        ws = _ws(TAB)
        col = ws.col_values(1)
        if rid not in col:
            return None
        row = col.index(rid) + 1
        cur = dict(zip(COLS, ['' if v is None else v for v in ws.row_values(row, value_render_option='UNFORMATTED_VALUE')] + [''] * len(COLS)))
        if expect and cur['status'] != expect:
            return None
        cur.update(fields)
        for k in NUMERIC:
            cur[k] = _num(cur[k]) if cur[k] != '' else ''
        ws.update([[_cell(cur[c]) for c in COLS]], f'A{row}', value_input_option='RAW')
        refresh()
        return cur


def delete(rid, who):
    """Mark a report Deleted: it drops out of every list. The row stays (and its number is never reused)."""
    r = get(rid)
    update(rid, status='Deleted', closed_by=who)
    return r


def add_files(rid, files):
    files = [f for f in files if f and f.filename]
    if not files:
        return 0
    r = get(rid)
    m = re.search(r'/folders/([\w-]+)', r.get('files_url') or '')
    folder_id, url = (m.group(1), r['files_url']) if m else (None, '')
    if not folder_id:
        folder = drive_folder(f"{rid} - {r.get('event_name') or r.get('program', '')}"[:120], os.environ['FUNDS_RECEIPTS_FOLDER_ID'])
        folder_id, url = folder['id'], folder['webViewLink']
    for f in files:
        drive_upload(f, folder_id)
    if url != r.get('files_url'):
        update(rid, files_url=url)
    return len(files)


def match_deposits(reps, deposits, used=(), days=45, taken=()):
    """Reports still Received that a QuickBooks deposit took in: [(report, {'deposit_date', 'deposit_ref', 'deposit_amount'})].

    A report matches a deposit line, or a whole deposit (its lines added up), of exactly its total, dated on or after
    the day it was received and within `days`. Only a single unambiguous match counts and each deposit is used once:
    deposits that reports already Deposited used (tonight or on an earlier night) are left out, as are those in
    `taken`, (ref, date, amount) of the invoices already paid, and the deposit ids in `used`.
    """
    deposits = qbo_logic.unused_deposits(deposits, [(r.get('deposit_ref'), r.get('deposit_date'), r.get('deposit_amount'))
                                                    for r in reps if r['status'] == 'Deposited'] + list(taken))
    used = set(used)
    by_dep = {}
    for d in deposits:
        by_dep.setdefault(d['id'], []).append(d)
    whole = [{'id': k, 'date': v[0]['date'], 'amount': round(sum(x['amount'] for x in v), 2), 'line': None} for k, v in by_dep.items()]
    single = [{'id': d['id'], 'date': d['date'], 'amount': round(d['amount'], 2), 'line': (d['id'], d['description'], d['amount'])}
              for d in deposits]
    out = []
    for r in sorted(reps, key=lambda r: r['received']):
        if r['status'] != 'Received' or not r['total'] or not r['received']:
            continue
        last = date.fromisoformat(r['received'][:10]).toordinal() + days
        cands = [c for c in single + whole if c['id'] not in used and (c['line'] or c['id']) not in used
                 and abs(c['amount'] - r['total']) < 0.005 and r['received'][:10] <= c['date'] <= date.fromordinal(last).isoformat()]
        ids = {c['id'] for c in cands}
        if len(ids) == 1:
            c = cands[0]
            used.add(c['id'])
            out.append((r, {'deposit_date': c['date'], 'deposit_ref': qbo_logic.deposit_ref(c['id'], c['date']), 'deposit_amount': c['amount']}))
    return out
