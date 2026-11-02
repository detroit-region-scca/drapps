"""The private DRSCCA Members sheet (MEMBERS_SHEET_ID) and the public drscca.org sheet (PUBLIC_SHEET_ID).

Members sheet tabs (see members_logic for the columns):
  Members  one row per member; the Points column is a formula over the Points tab (Approved entries only)
  Points   every points entry, one row each
  Auto add people who get points every month (Member # | Name | Role | Apr-Oct | Nov-Mar); the app prepares them as Pending
The sheet stays usable by hand if the apps are gone. The public sheet holds what drscca.org shows:
  Member Milestones  Month | Name | Years | Note | Points through | Published
"""
import logging
import os
import threading
from datetime import date

from storage import worksheet
import members_logic as L
import sheet_cache as C

_lock = threading.Lock()
log = logging.getLogger(__name__)


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
    """Append entries (dicts from members_logic.rows_from_grid, or the same keys). Returns how many."""
    values = [[(e['date'] or date.today()).isoformat(), _text(e['member']), _text(e['name']), float(e['points']), _text(e['what']),
               _text(e.get('event', '')), e.get('kind') or ('Used' if e['points'] < 0 else 'Earned'), status, _text(who)] for e in rows]
    if not values:
        return 0
    with _lock:
        worksheet(_sid(), 'Points').append_rows(values, value_input_option='RAW')
        refresh()
    return len(values)


def delete_rows(row_numbers):
    with _lock:
        ws = worksheet(_sid(), 'Points')
        for n in sorted(row_numbers, reverse=True):
            ws.delete_rows(n)
        refresh()


def write_members(rows):
    """Rewrite the Members tab (rows = member dicts), keeping the Points formula on every row."""
    def d(v):
        return v.isoformat() if v else ''
    values = [L.MEMBER_COLS]
    for i, m in enumerate(rows, start=2):
        values.append(["'" + str(m['member']), _text(m['first']), _text(m['last']), 'LIFE' if m.get('life') else '', d(m.get('joined')),
                       d(m.get('expires')), d(m.get('listed')), f'=SUMIFS(Points!D:D,Points!B:B,A{i},Points!H:H,"{L.APPROVED}")',
                       d(m.get('lapsed')), d(m.get('rejoined'))])
    with _lock:
        ws = worksheet(_sid(), 'Members')
        ws.batch_clear(['A:J'])
        if ws.col_count < 10:
            ws.add_cols(10 - ws.col_count)
        if ws.row_count < len(values) + 10:
            ws.add_rows(len(values) + 50 - ws.row_count)
        ws.update(values, 'A1', value_input_option='USER_ENTERED')
        refresh()


def add_member(mid, first, last, life=False):
    with _lock:
        ws = worksheet(_sid(), 'Members')
        i = len(ws.col_values(1)) + 1
        ws.update([["'" + mid, _text(first), _text(last), 'LIFE' if life else '', '', '', '',
                    f'=SUMIFS(Points!D:D,Points!B:B,A{i},Points!H:H,"{L.APPROVED}")']], f'A{i}', value_input_option='USER_ENTERED')
        refresh()


# ---------- the public drscca.org sheet ----------

def _pub():
    return os.environ['PUBLIC_SHEET_ID']


def milestone_rows():
    vals = worksheet(_pub(), 'Member Milestones').get_all_values()
    return [r for r in vals[1:] if any(str(c).strip() for c in r)]


def save_milestones(month, new_rows):
    """Replace one month on the public Member Milestones tab (newest month first)."""
    with _lock:
        ws = worksheet(_pub(), 'Member Milestones')
        keep = [list(r) for r in milestone_rows() if str((list(r) + [''])[0]).strip() != month]
        out = sorted(keep + [list(r) for r in new_rows], key=lambda r: str(r[0]), reverse=True)
        ws.batch_clear(['A2:D'])
        if out:
            ws.update([[int(c) if i == 2 and str(c).strip().isdigit() else str(c) for i, c in enumerate((list(r) + [''] * 4)[:4])] for r in out],
                      'A2', value_input_option='RAW')
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


def auto_rows():
    out = []
    for i, r in enumerate(_auto_ws().get_all_values()[1:], start=2):
        r = list(r) + [''] * 5
        if str(r[0]).strip() or str(r[2]).strip():
            out.append({'row': i, 'member': str(r[0]).strip().lstrip("'"), 'name': r[1], 'role': r[2], 'season': L.num(r[3]), 'offseason': L.num(r[4])})
    return out


def save_auto_rows(rows):
    ws = _auto_ws()
    ws.batch_clear(['A2:E'])
    if rows:
        ws.update([["'" + r['member'], _text(r['name']), _text(r['role']), r['season'], r['offseason']] for r in rows], 'A2',
                  value_input_option='USER_ENTERED')


def prepare_auto(year, month, who='Auto add (monthly)'):
    """Add this month's auto-add points (once). They count right away: the user asked for these to be automatic."""
    rows = L.auto_entries(auto_rows(), entries(), year, month)
    return add_entries(rows, L.APPROVED, who) if rows else 0


def mark_reconciled(row_numbers, label):
    """Fill the Reconciled column (e.g. "2026-12-01 JE 2041") for these Points rows."""
    with _lock:
        ws = worksheet(_sid(), 'Points')
        if ws.col_count < 10:
            ws.add_cols(10 - ws.col_count)
        ws.batch_update([{'range': f'J{n}', 'values': [[_text(label)]]} for n in row_numbers], value_input_option='RAW')
        refresh()
