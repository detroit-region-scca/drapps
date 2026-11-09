"""Board email votes, kept in the DRSCCA Annual Plan sheet (PLAN_SHEET_ID) beside the Motions tab, so anyone with
Drive access can read the roll call without the app.

- 'Ballots': one row per item put to an email vote (a budget or a motion): when it opened and closes, its status, and
  the result once decided. 'snap' is a fingerprint of a budget's numbers when its vote opened (blank for a motion and
  for votes opened before it was kept), so a budget changed during the vote is not approved by it.
- 'Ballot votes': one row per vote cast (a director may change their vote while it is open; the last one counts).
Nothing is deleted: a vote that is no longer wanted is cancelled, one that ran out of time is expired.
"""
import os
import threading
from datetime import datetime

import gspread
from filelock import FileLock

import ballots_logic as BL
import sheet_cache as C
from storage import DATA_DIR, open_book, worksheet

TAB, VOTES_TAB = 'Ballots', 'Ballot votes'
COLS = ['id', 'item', 'title', 'opened', 'opened_by', 'closes', 'status', 'result', 'count', 'decided', 'recorded_by', 'snap']
VOTE_COLS = ['ballot_id', 'email', 'name', 'choice', 'at', 'via']
SAVE_LOCK = os.path.join(DATA_DIR, 'ballots_save.lock')
_lock = threading.Lock()


def enabled():
    return bool(os.environ.get('PLAN_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _sheet():
    return os.environ['PLAN_SHEET_ID']


def _ws(title, cols):
    try:
        return worksheet(_sheet(), title)
    except gspread.exceptions.WorksheetNotFound:
        ws = open_book(_sheet()).add_worksheet(title, rows=200, cols=len(cols))
        ws.update([cols], 'A1', value_input_option='RAW')
        return ws


def _rows(title, cols):
    try:
        # The whole tab, not A:L: a tab that does not have the newest column yet still reads (asking past a tab's last
        # column is refused, which would hide every ballot).
        res = C.batch_get(_sheet(), [f"'{title}'"], params={'valueRenderOption': 'FORMATTED_VALUE'})
    except gspread.exceptions.APIError:
        return []      # the tab is made by the first email vote
    vals = (res.get('valueRanges') or [{}])[0].get('values', [])
    return [dict(zip(cols, [str(v) for v in r] + [''] * len(cols))) for r in vals[1:] if r and str(r[0]).strip()]


def ballots():
    return _rows(TAB, COLS)


def votes(ballot_id=None):
    vs = _rows(VOTES_TAB, VOTE_COLS)
    return [v for v in vs if v['ballot_id'] == ballot_id] if ballot_id else vs


def get(ballot_id):
    return next((b for b in ballots() if b['id'] == ballot_id), None)


def current(item):
    """The latest ballot for an item ('budget:<year>:<code>' or 'motion:<id>') that is not cancelled, or None."""
    bs = [b for b in ballots() if b['item'] == item and b['status'] != 'cancelled']
    return max(bs, key=lambda b: b['id']) if bs else None


def _next_id(ids, year):
    n = max([int(i.rsplit('-', 1)[1]) for i in ids if i.startswith(f'B-{year}-') and i.rsplit('-', 1)[1].isdigit()] or [0])
    return f'B-{year}-{n + 1:03d}'


def open_ballot(item, title, who, today, snap=''):
    """Open an email vote on an item and return its id. An earlier ballot on the same item that is still open or
    expired is cancelled, so only one counts. snap: the budget's fingerprint as it is put to the vote."""
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws(TAB, COLS)
        _ws(VOTES_TAB, VOTE_COLS)
        if ws.col_count < len(COLS):          # a tab made before a column was added: widen it first, or the write is refused
            ws.add_cols(len(COLS) - ws.col_count)
        rows = ws.get_all_values()
        if rows and len(rows[0]) < len(COLS):
            ws.update([COLS[len(rows[0]):]], f'{chr(65 + len(rows[0]))}1', value_input_option='RAW')
        for i, r in enumerate(rows[1:], start=2):
            r = r + [''] * len(COLS)
            if r[1] == item and r[6] in ('open', '', 'expired'):
                ws.update([['cancelled']], f'G{i}', value_input_option='RAW')
        bid = _next_id([r[0] for r in rows[1:] if r], int(today[:4]))
        ws.append_row([bid, item, title, today, who, BL.closes(today), 'open', '', '', '', '', snap],
                      value_input_option='RAW', table_range='A1')
        C.invalidate(_sheet())
        return bid


def update(ballot_id, **fields):
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws(TAB, COLS)
        ids = ws.col_values(1)
        if ballot_id not in ids:
            raise KeyError(ballot_id)
        row = ids.index(ballot_id) + 1
        ws.batch_update([{'range': f'{chr(65 + COLS.index(k))}{row}', 'values': [['' if v is None else v]]}
                         for k, v in fields.items() if k in COLS and k != 'id'], value_input_option='RAW')
        C.invalidate(_sheet())


def cast(ballot_id, email, name, choice, via):
    """Record a vote (appended; the last vote per person counts)."""
    if choice not in BL.CHOICES:
        raise ValueError(choice)
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws(VOTES_TAB, VOTE_COLS)
        ws.append_row([ballot_id, email.lower(), name, choice, datetime.now().strftime('%Y-%m-%d %H:%M'), via],
                      value_input_option='RAW', table_range='A1')
        C.invalidate(_sheet())
