"""The private DRSCCA Members sheet (MEMBERS_SHEET_ID) and the public drscca.org sheet (PUBLIC_SHEET_ID).

Members sheet tabs (see members_logic for the columns):
  Members  one row per member; the Points column is a formula over the Points tab (Approved entries only)
  Points   every points entry, one row each
  Auto add people who get points every month (Member # | Name | Role | Apr-Oct | Nov-Mar); the app adds them on the 1st, Approved
The sheet stays usable by hand if the apps are gone. The public sheet holds what drscca.org shows:
  Member Milestones  Month | Name | Years | Note | Points through | Published
"""
import logging
import os
import threading
from datetime import date

from filelock import FileLock

from storage import worksheet
import members_logic as L
import sheet_cache as C

_lock = threading.Lock()
log = logging.getLogger(__name__)
SAVES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'points-saves.json')
# The app runs several workers, so a thread lock alone protects nothing: every read-check-write of the Members sheet
# takes this file lock (beside SAVES_FILE). Never take it twice in one call (add_entries is called with it held and
# must not take it).


def _file_lock():
    os.makedirs(os.path.dirname(SAVES_FILE), exist_ok=True)
    return FileLock(SAVES_FILE + '.lock', timeout=60)


def enabled():
    return bool(os.environ.get('MEMBERS_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _sid():
    return os.environ['MEMBERS_SHEET_ID']


def refresh():
    C.invalidate(_sid())


def _text(v):
    v = str(v if v is not None else '')
    return "'" + v if v[:1] in ('=', '+', '-', '@') else v


def _snapshot():
    res = C.batch_get(_sid(), ['Members!A:J', 'Points!A:J'], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    vr = [r.get('values', []) for r in res.get('valueRanges', [])]
    return vr + [[]] * (2 - len(vr))


def members():
    out = []
    for r in _snapshot()[0][1:]:
        r = list(r) + [''] * 10
        mid = str(r[0]).strip()
        if not mid:
            continue
        out.append({'member': mid, 'first': str(r[1]), 'last': str(r[2]), 'life': str(r[3]).strip().upper() in ('LIFE', 'YES', 'TRUE'),
                    'joined': L.to_date(r[4]), 'expires': L.to_date(r[5]), 'listed': L.to_date(r[6]), 'points': round(L.num(r[7]), 3),
                    'lapsed': L.to_date(r[8]), 'rejoined': L.to_date(r[9])})
    return out


def entries():
    out = []
    for i, r in enumerate(_snapshot()[1][1:], start=2):
        r = list(r) + [''] * 10
        if not str(r[1]).strip() and not str(r[2]).strip():
            continue
        out.append({'row': i, 'date': L.to_date(r[0]), 'member': str(r[1]).strip(), 'name': str(r[2]), 'points': L.num(r[3]),
                    'what': str(r[4]), 'event': str(r[5]), 'kind': str(r[6]), 'status': str(r[7]).strip(), 'by': str(r[8]),
                    'reconciled': str(r[9]).strip()})
    return out


def add_entries(rows, status, who):
    """Append entries ({'date', 'member', 'name', 'points', 'what', 'event', 'kind'} dicts). Returns how many."""
    values = [[(e['date'] or date.today()).isoformat(), _text(e['member']), _text(e['name']), float(e['points']), _text(e['what']),
               _text(e.get('event', '')), e.get('kind') or ('Used' if e['points'] < 0 else 'Earned'), status, _text(who)] for e in rows]
    if not values:
        return 0
    with _lock:
        worksheet(_sid(), 'Points').append_rows(values, value_input_option='RAW')
        refresh()
    return len(values)


def save_checked(build, who, token=''):
    """Add points with the check and the write under one lock shared by every server process: read the Points tab
    fresh, build(entries) -> (rows, problems), append only when there are no problems. token: the review page's one-time
    token; a page already saved (a double click, the back button, a second tab) is refused. Returns (count, problems).
    Using and forfeiting points come through here too, so a balance is always checked on a fresh read."""
    import json
    import time
    with _file_lock():
        try:
            used = json.load(open(SAVES_FILE))
        except (OSError, ValueError):
            used = {}
        if token and token in used:
            return 0, ['ALREADY_SAVED']
        refresh()
        rows, problems = build(entries())
        if problems or not rows:
            return 0, problems
        def write(d):
            tmp = SAVES_FILE + '.tmp'
            json.dump(d, open(tmp, 'w'))
            os.replace(tmp, SAVES_FILE)
        if token:                 # recorded before the append: a retry after a timeout is refused, not saved twice
            now = time.time()
            used = {k: v for k, v in used.items() if now - v < 90 * 86400}
            write(dict(used, **{token: now}))
        try:
            n = add_entries(rows, L.APPROVED, who)
        except Exception:
            if token:             # nothing reached the sheet as far as we know: let the page be saved again (the
                write(used)       # fresh duplicate check still guards an event if the append did land)
            raise
        return n, []


def write_members(rows):
    """Rewrite the Members tab (rows = member dicts), keeping the Points formula on every row. The new rows are written
    over the old ones first and only then is anything left below them cleared, so a failure part way never leaves the
    tab empty."""
    def d(v):
        return v.isoformat() if v else ''
    values = [L.MEMBER_COLS]
    for i, m in enumerate(rows, start=2):
        values.append(["'" + str(m['member']), _text(m['first']), _text(m['last']), 'LIFE' if m.get('life') else '', d(m.get('joined')),
                       d(m.get('expires')), d(m.get('listed')), f'=SUMIFS(Points!D:D,Points!B:B,A{i},Points!H:H,"{L.APPROVED}")',
                       d(m.get('lapsed')), d(m.get('rejoined'))])
    with _lock:
        ws = worksheet(_sid(), 'Members')
        if ws.col_count < 10:
            ws.add_cols(10 - ws.col_count)
        if ws.row_count < len(values) + 10:
            ws.add_rows(len(values) + 50 - ws.row_count)
        ws.update(values, 'A1', value_input_option='USER_ENTERED')
        ws.batch_clear([f'A{len(values) + 1}:J'])
        refresh()


def update_members(merge):
    """Read the Members tab fresh, merge(rows) -> the new rows, and write them, as one step across workers: a member
    added while a list is loading, or two loads at once, cannot undo each other. Returns (rows before, rows after)."""
    with _file_lock():
        refresh()
        before = members()
        after = merge(before)
        write_members(after)
    return before, after


def add_member(mid, first, last, life=False):
    """Add a member row. If the member is already there (SCCA's 500100_0 and the ledger's 500100 are one member),
    nothing is added and the number already on the tab is returned; else None."""
    with _file_lock(), _lock:
        ws = worksheet(_sid(), 'Members')
        col = ws.col_values(1)
        same = next((str(v).strip() for v in col[1:] if str(v).strip() and L.canon(v) == L.canon(mid)), None)
        if same:
            return same
        i = len(col) + 1
        ws.update([["'" + mid, _text(first), _text(last), 'LIFE' if life else '', '', '', '',
                    f'=SUMIFS(Points!D:D,Points!B:B,A{i},Points!H:H,"{L.APPROVED}")']], f'A{i}', value_input_option='USER_ENTERED')
        refresh()
    return None


# ---------- the public drscca.org sheet ----------

def _pub():
    return os.environ['PUBLIC_SHEET_ID']


def milestone_rows(fresh=False):
    """The public Member Milestones tab, from the shared local copy (fresh=True reads Google, for a write)."""
    res = C.batch_get(_pub(), ["'Member Milestones'!A:D"], fresh=fresh)
    vals = (res.get('valueRanges') or [{}])[0].get('values', [])
    return [r for r in vals[1:] if any(str(c).strip() for c in r)]


def save_milestones(month, new_rows):
    """Replace one month on the public Member Milestones tab (newest month first). Written over the old rows first,
    then what is left below is cleared, so a failure part way never leaves the public list empty."""
    with _file_lock(), _lock:
        ws = worksheet(_pub(), 'Member Milestones')
        keep = [list(r) for r in milestone_rows(fresh=True) if str((list(r) + [''])[0]).strip() != month]
        out = sorted(keep + [list(r) for r in new_rows], key=lambda r: str(r[0]), reverse=True)
        if ws.row_count < len(out) + 2:
            ws.add_rows(len(out) + 20 - ws.row_count)
        if out:
            ws.update([[int(c) if i == 2 and str(c).strip().isdigit() else str(c) for i, c in enumerate((list(r) + [''] * 4)[:4])] for r in out],
                      'A2', value_input_option='RAW')
        ws.batch_clear([f'A{len(out) + 2}:D'])
        C.invalidate(_pub())
    return len(new_rows)


def public_sheet_url():
    return f"https://docs.google.com/spreadsheets/d/{os.environ.get('PUBLIC_SHEET_ID', '')}/edit"


def members_sheet_url():
    return f"https://docs.google.com/spreadsheets/d/{os.environ.get('MEMBERS_SHEET_ID', '')}/edit"


# ---------- the original SCCA member list files (private folder in the DRSCCA Apps drive) ----------

def save_list_file(data, uploaded_by):
    import json as _json
    from storage import UPLOAD, sheets_client
    name = f'{date.today().isoformat()} SCCA Member List.xlsx'
    files = {'metadata': ('metadata', _json.dumps({'name': name, 'parents': [os.environ['MEMBER_LISTS_FOLDER_ID']],
                                                    'description': f'Uploaded in DRSCCA Apps by {uploaded_by}'}), 'application/json'),
             'file': (name, data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
    r = sheets_client().http_client.session.post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true',
                                                                  'fields': 'id,name,webViewLink'}, files=files)
    r.raise_for_status()
    return r.json()


def list_folder_url():
    return f"https://drive.google.com/drive/folders/{os.environ.get('MEMBER_LISTS_FOLDER_ID', '')}"


def _auto_ws():
    from storage import open_book
    try:
        return worksheet(_sid(), 'Auto add')
    except Exception:
        ws = open_book(_sid()).add_worksheet('Auto add', rows=50, cols=5)
        ws.update([L.AUTO_COLS], 'A1')
        ws.freeze(rows=1)
        ws.format('A1:E1', {'textFormat': {'bold': True}})
        from storage import _tabs
        _tabs[(_sid(), 'Auto add')] = ws
        return ws


def auto_rows(fresh=False):
    """The Auto add tab, from the shared local copy (made on first use if the tab is missing)."""
    import gspread
    try:
        res = C.batch_get(_sid(), ["'Auto add'!A:E"], fresh=fresh)
        vals = (res.get('valueRanges') or [{}])[0].get('values', [])
    except gspread.exceptions.APIError:
        vals = _auto_ws().get_all_values()
        C.invalidate(_sid())
    out = []
    for i, r in enumerate(vals[1:], start=2):
        r = list(r) + [''] * 5
        if str(r[0]).strip() or str(r[2]).strip():
            out.append({'row': i, 'member': str(r[0]).strip().lstrip("'"), 'name': r[1], 'role': r[2], 'season': L.num(r[3]), 'offseason': L.num(r[4])})
    return out


def save_auto_rows(rows):
    """Replace the Auto add list: the new rows first, then clear what is left below them."""
    with _file_lock():
        ws = _auto_ws()
        if ws.row_count < len(rows) + 2:
            ws.add_rows(len(rows) + 20 - ws.row_count)
        if rows:
            ws.update([["'" + r['member'], _text(r['name']), _text(r['role']), r['season'], r['offseason']] for r in rows], 'A2',
                      value_input_option='USER_ENTERED')
        ws.batch_clear([f'A{len(rows) + 2}:E'])
        C.invalidate(_sid())


def prepare_auto(year, month, who='Auto add (monthly)'):
    """Add this month's auto-add points (once). They count right away: the user asked for these to be automatic.
    Checked and added under the lock on a fresh read, so the button and the 1st-of-month job cannot both add them."""
    auto = auto_rows(fresh=True)
    return save_checked(lambda fresh: (L.auto_entries(auto, fresh, year, month), []), who)[0]


def mark_reconciled(row_numbers, label, keys=None):
    """Fill the Reconciled column (e.g. "2026-12-01 JE 2041") for these Points rows. Row numbers move when someone
    sorts or deletes rows in the sheet, so this reads the tab fresh under the lock and marks a row only if it is still
    an entry waiting to be booked. keys: {row: members_logic.entry_key(entry)} as the page showed them; a row that is
    no longer that entry is looked up by its key instead, and left alone when it cannot be found.
    Returns (rows marked, how many were left alone)."""
    keys = keys or {}
    with _file_lock():
        refresh()
        waiting = {e['row']: L.entry_key(e) for e in L.unreconciled(entries())}
        marked = [n for n in dict.fromkeys(row_numbers) if n in waiting and (not keys.get(n) or waiting[n] == keys[n])]
        for n in row_numbers:          # the rest moved (or are gone): find each by its key
            if not keys.get(n) or waiting.get(n) == keys[n]:
                continue
            same = [r for r, wk in waiting.items() if wk == keys[n] and r not in marked]
            if len(same) == 1:
                marked.append(same[0])
        if marked:
            with _lock:
                ws = worksheet(_sid(), 'Points')
                if ws.col_count < 10:
                    ws.add_cols(10 - ws.col_count)
                ws.batch_update([{'range': f'J{n}', 'values': [[_text(label)]]} for n in marked], value_input_option='RAW')
                refresh()
    return marked, len(set(row_numbers)) - len(marked)
