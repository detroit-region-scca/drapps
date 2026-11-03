"""Board motions: decisions the board votes on that are not a budget (setting money aside, a big purchase, a policy,
an out-of-region event). Kept in the 'Motions' tab of the DRSCCA Annual Plan sheet (PLAN_SHEET_ID).

A motion is proposed by anyone signed in, sent to the board for a vote, and its result (date, board meeting or email
vote, the count, passed or failed) recorded by the RE, ARE or treasurer. Nothing is deleted: a motion no longer wanted
is withdrawn."""
import os
import threading
from datetime import date

import gspread
from filelock import FileLock

import sheet_cache as C
from storage import DATA_DIR, open_book, worksheet

TAB = 'Motions'
COLS = ['id', 'proposed', 'proposed_by', 'title', 'details', 'amount', 'link', 'status', 'decided', 'how', 'vote',
        'recorded_by', 'asked']
STATUSES = ('open', 'passed', 'failed', 'withdrawn')
HOW = {'meeting': 'board meeting', 'email': 'email vote'}
SAVE_LOCK = os.path.join(DATA_DIR, 'motions_save.lock')
_lock = threading.Lock()


def enabled():
    return bool(os.environ.get('PLAN_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _sheet():
    return os.environ['PLAN_SHEET_ID']


def _ws():
    try:
        return worksheet(_sheet(), TAB)
    except gspread.exceptions.WorksheetNotFound:
        ws = open_book(_sheet()).add_worksheet(TAB, rows=200, cols=len(COLS))
        ws.update([COLS], 'A1', value_input_option='RAW')
        return ws


def _text(c, v):
    if c in ('proposed', 'decided', 'asked') and isinstance(v, (int, float)) and not isinstance(v, bool) and 20000 < v < 80000:
        from datetime import timedelta
        return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
    return '' if v is None else str(v)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def motions():
    """Every motion, newest first."""
    try:
        res = C.batch_get(_sheet(), [f"'{TAB}'!A:M"], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    except gspread.exceptions.APIError:
        return []      # no Motions tab until the first motion is proposed
    vals = (res.get('valueRanges') or [{}])[0].get('values', [])
    out = []
    for r in vals[1:]:
        if not r or not str(r[0]).strip():
            continue
        m = {c: _text(c, v) for c, v in zip(COLS, list(r) + [''] * len(COLS))}
        m['amount'] = _num(m['amount'])
        m['status'] = m['status'] if m['status'] in STATUSES else 'open'
        out.append(m)
    out.sort(key=lambda m: m['id'], reverse=True)
    return out


def get(mid):
    return next((m for m in motions() if m['id'] == mid), None)


def _next_id(ids, year):
    n = max([int(i.rsplit('-', 1)[1]) for i in ids if i.startswith(f'M-{year}-') and i.rsplit('-', 1)[1].isdigit()] or [0])
    return f'M-{year}-{n + 1:03d}'


def propose(title, details, amount, link, who):
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws()
        ids = ws.col_values(1)[1:]
        today = date.today().isoformat()
        mid = _next_id(ids, date.today().year)
        ws.append_row([mid, today, who, title, details, '' if amount is None else amount, link, 'open', '', '', '', '', ''],
                      value_input_option='RAW', table_range='A1')
        C.invalidate(_sheet())
        return mid


def update(mid, **fields):
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws()
        ids = ws.col_values(1)
        if mid not in ids:
            raise KeyError(mid)
        row = ids.index(mid) + 1
        ws.batch_update([{'range': f'{chr(65 + COLS.index(k))}{row}', 'values': [['' if v is None else v]]}
                         for k, v in fields.items() if k in COLS and k != 'id'], value_input_option='RAW')
        C.invalidate(_sheet())


def summary(m):
    """One line for an email or a list: the motion's title (it carries any amounts)."""
    return m['title']
