"""Reads and writes the DRSCCA Annual Plan Google Sheet (PLAN_SHEET_ID) in the DRSCCA Apps Shared Drive.

  Plan            one row per planned event or item: year, program, item, month, net (+ profit, - cost)
  Programs        one row per program and year: director, email, notes for the board, last update, turned in
  QBO Programs    the region profit and loss by account and year      } written nightly by
  QBO Monthly     profit by program and month, this year and last      }
  QBO Status      bank balances and the worker points reserve          } tools/qbo_nightly.py,
  QBO Exceptions  things in the books the treasurer should look at     } read only here
  Reviewed        exception keys the treasurer has looked at, so they stay hidden
"""
import os
import re
import threading
from datetime import date

import gspread
from filelock import FileLock

import bud_store
import qbo_logic
from storage import DATA_DIR, open_book, worksheet
import sheet_cache as C

PLAN_COLS = ['year', 'program', 'item', 'month', 'net', 'event_code', 'note', 'sort', 'account']    # account: the QuickBooks account it is booked to
PROG_COLS = ['year', 'program', 'director', 'email', 'notes', 'updated', 'updated_by', 'submitted', 'submitted_by', 'approved', 'approved_by',
             'approved_hash']
# approved_hash (added last, 2026-11): a fingerprint of the plan lines the board approved (Operations only), see
# bud_store.lines_hash. A row approved before it existed has none and is compared by day, as before.
EXC_COLS = ['key', 'kind', 'date', 'name', 'amount', 'detail', 'link', 'as_of']
SAVE_LOCK = os.path.join(DATA_DIR, 'plan_save.lock')
# The whole Programs tab, not A:L: a tab that does not have the newest column yet still reads.
RANGES = ['Plan!A:I', "'Programs'", 'QBO Programs!A:F', 'QBO Status!A:C', 'QBO Exceptions!A:H', 'Reviewed!A:D', 'QBO Monthly!A:E']

_lock = threading.Lock()
_cache = {}


def enabled():
    return bool(os.environ.get('PLAN_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _ws(title):
    return worksheet(os.environ['PLAN_SHEET_ID'], title)


def refresh():
    _cache.clear()
    C.invalidate(os.environ['PLAN_SHEET_ID'])


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows(values, cols):
    return [dict(zip(cols, list(r) + [''] * len(cols))) for r in values[1:] if r and str(r[0]).strip()]


def _snapshot():
    hit, ver = _cache.get('snap'), C.version(os.environ['PLAN_SHEET_ID'])      # the version first: a write during the read below must not look fresh
    if hit and hit[0] == ver:
        return hit[1]
    try:
        res = C.batch_get(os.environ['PLAN_SHEET_ID'], RANGES, params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    except gspread.exceptions.APIError:
        # No QBO Monthly tab until the first sync that writes it.
        res = C.batch_get(os.environ['PLAN_SHEET_ID'], RANGES[:-1], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    vr = [r.get('values', []) for r in res.get('valueRanges', [])]
    lines = _rows(vr[0], PLAN_COLS)
    for ln in lines:
        ln['year'], ln['program'], ln['item'], ln['note'] = str(ln['year']), str(ln['program']), str(ln['item']), str(ln['note'])
        ln['net'], ln['sort'] = _num(ln['net']), _num(ln['sort']) or 0
        ln['month'] = int(ln['month']) if _num(ln['month']) else None
        ln['event_code'], ln['account'] = str(ln['event_code']), str(ln['account'])
    progs = _rows(vr[1], PROG_COLS)
    for p in progs:
        p.update({k: str(p[k]) for k in PROG_COLS})
    qbo = _rows(vr[2], ['year', 'program', 'section', 'account', 'amount', 'as_of'])
    status = {str(r['item']): {'amount': _num(r['amount']), 'as_of': str(r['as_of'])} for r in _rows(vr[3], ['item', 'amount', 'as_of'])}
    exc = _rows(vr[4], EXC_COLS)
    for e in exc:
        e.update({k: str(e[k]) for k in EXC_COLS if k != 'amount'}, amount=_num(e['amount']))
    reviewed = {str(r['key']): r for r in _rows(vr[5], ['key', 'reviewed', 'by', 'note'])}
    monthly = _rows(vr[6], ['year', 'program', 'month', 'net', 'as_of']) if len(vr) > 6 else []
    snap = (lines, progs, qbo, status, exc, reviewed, monthly)
    _cache['snap'] = (ver, snap)
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
    for prog, p in out.items():
        # Operations lines with no QuickBooks account are money moved to savings (the equipment replacement set-aside):
        # still the region's money, so they are shown on their own and kept out of the budget's profit or loss.
        save = [ln for ln in p['lines'] if prog == 'OPS' and not ln.get('account') and not ln.get('event_code')]
        for ln in save:
            ln['savings'] = True
        p['savings'] = round(-sum(ln['net'] or 0 for ln in save), 2)
        p['net'] = round(sum(ln['net'] or 0 for ln in p['lines'] if not ln.get('savings')), 2)
    for info in progs:
        if info['year'] == str(year):
            p = out.setdefault(info['program'], {'lines': [], 'net': 0.0, 'savings': 0.0})
            # 'changed' is worked out here, where the lines are, for plan_status (which only gets the info).
            p['info'] = dict(info, changed=bool(info.get('approved')) and bud_store.changed_since_approved(info, lines_hash(p['lines'])))
    for p in out.values():
        p.setdefault('info', {})
    _add_program_budgets(str(year), out)
    return out


def _add_program_budgets(year, out):
    """Each program's budget for spending not in an event (kept in the Event Budgets sheet as RY-2027 and so on) is a
    plan line of its own, its budgeted surplus, so nobody types it into the plan twice. It is not saved in the plan:
    the edit page leaves it out and the budget page changes it. Operations has none: its plan is its budget."""
    import bud_store as B
    if not B.enabled():
        return
    for code, b in B.budgets().items():
        m = re.match(r'^([A-Z]{2,5})-(\d{4})$', code)
        if not m or m.group(2) != year or m.group(1) == 'OPS':
            continue
        net = (b['totals'].get('budget') or {}).get('surplus')
        p = out.setdefault(m.group(1), {'lines': [], 'net': 0.0, 'info': {}})
        p['lines'].append({'year': year, 'program': m.group(1), 'item': 'Program budget, not in an event', 'month': None,
                           'net': net, 'event_code': '', 'note': '', 'sort': 10 ** 6, 'account': '', 'program_budget': code})
        p['net'] = round(sum(ln['net'] or 0 for ln in p['lines']), 2)


def saved_lines(lines):
    """Plan lines as directors typed them, without the program budget line."""
    return [ln for ln in lines if not ln.get('program_budget')]


def actuals(year):
    """{program: {'income', 'expense', 'net', 'lines'}} from QuickBooks for one year, plus 'as_of'."""
    rows = [r for r in _snapshot()[2] if str(r['year']) == str(year)]
    lines = [{'section': str(r['section']), 'account': str(r['account']), 'amount': _num(r['amount']) or 0.0} for r in rows]
    return qbo_logic.program_totals(lines), (str(rows[0]['as_of']) if rows else '')


def monthly(year):
    """{program: [net for Jan..Dec]} from QuickBooks for one year."""
    out = {}
    for r in _snapshot()[6]:
        if str(r['year']) == str(year) and _num(r['month']):
            out.setdefault(str(r['program']), [0.0] * 12)[int(_num(r['month'])) - 1] += _num(r['net']) or 0
    return out


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


def lines_hash(lines):
    """The fingerprint of a plan's lines (item, account, amount) that "changed since approved" is judged against."""
    return bud_store.lines_hash(lines, text=('item', 'account'), amount='net')


def _programs_ws():
    """The Programs tab, wide enough for every column, with a heading on columns added later."""
    ws = _ws('Programs')
    if not _cache.get('heading_ok'):
        if ws.col_count < len(PROG_COLS):          # the tab must be wide enough first, or the write is refused
            ws.add_cols(len(PROG_COLS) - ws.col_count)
        top = ws.row_values(1)
        if len(top) < len(PROG_COLS):
            ws.update([PROG_COLS[len(top):]], f'{chr(ord("A") + len(top))}1', value_input_option='RAW')
        _cache['heading_ok'] = True
    return ws


def set_approved(year, program, approved, who):
    """Record (or clear, with approved='') the board meeting date that approved one program's plan for a year, with
    the fingerprint of its lines as they are right now."""
    year = str(year)
    with FileLock(SAVE_LOCK), _lock:
        refresh()
        lines = [ln for ln in _snapshot()[0] if ln['year'] == year and ln['program'] == program]
        progs = _snapshot()[1]
        at = next((i for i, p in enumerate(progs) if p['year'] == year and p['program'] == program), None)
        if at is None:
            raise ValueError('That plan has not been saved yet.')
        fresh = approved and str(progs[at].get('updated', '')) <= str(approved)      # edited after that meeting: shows as changed
        _programs_ws().update([[approved, who if approved else '', lines_hash(lines) if fresh else '']], f'J{at + 2}:L{at + 2}',
                              value_input_option='RAW')
        refresh()


def set_draft(year, program):
    """Put one program's plan (for Operations, its budget) back into draft: not turned in, and any board approval
    cleared."""
    year = str(year)
    with FileLock(SAVE_LOCK), _lock:
        refresh()
        progs = _snapshot()[1]
        at = next((i for i, p in enumerate(progs) if p['year'] == year and p['program'] == program), None)
        if at is None:
            raise ValueError('That plan has not been saved yet.')
        _programs_ws().update([['', '', '', '', '']], f'H{at + 2}:L{at + 2}', value_input_option='RAW')
        refresh()


def plan_status(info):
    """'approved', 'changed' (edited after the board approved it), 'in', 'draft' or '' (nothing saved).
    The board approves budgets, not plans: only Operations, whose plan is its budget, can be board approved."""
    if info.get('approved') and info.get('program') == 'OPS':
        # plan() works out 'changed' from the lines; an info from anywhere else falls back to comparing the days.
        changed = info['changed'] if 'changed' in info else bud_store.changed_since_approved(info, '')
        return 'changed' if changed else 'approved'
    return 'in' if info.get('submitted') else 'draft' if info.get('updated') else ''


def _cell(v):
    if v is None:
        return ''
    return v if isinstance(v, (int, float)) else str(v)


def save(year, program, info, lines, submit=False):
    """Replace one program's plan for one year: its Programs row upserted, its Plan rows rewritten.
    A save is a draft unless submit=True ("turn it in"); once turned in, later saves keep it turned in."""
    year = str(year)
    with FileLock(SAVE_LOCK), _lock:
        refresh()
        all_lines, progs = _snapshot()[:2]
        old = next((p for p in progs if p['year'] == year and p['program'] == program), {})
        old_lines = [ln for ln in all_lines if ln['year'] == year and ln['program'] == program]
        # Saving it again with nothing changed is not a change: the day (and who) stay, and an approved budget
        # stays approved.
        pick = lambda ls: [(ln.get('item') or '', ln.get('month') or '', ln.get('net'), ln.get('note') or '', ln.get('event_code') or '',  # noqa: E731
                            ln.get('account') or '') for ln in ls]
        same = bool(old.get('updated')) and pick(sorted(old_lines, key=lambda ln: ln['sort'])) == pick(lines) \
            and str(old.get('notes', '')) == str(info.get('notes', ''))
        who = info.get('updated_by', '')
        info = dict(info, year=year, program=program, updated=old['updated'] if same else date.today().isoformat(),
                    updated_by=old.get('updated_by', '') if same else info.get('updated_by', ''),
                    submitted=old.get('submitted', ''), submitted_by=old.get('submitted_by', ''),
                    approved=old.get('approved', ''), approved_by=old.get('approved_by', ''), approved_hash=old.get('approved_hash', ''))
        if old.get('approved') and not old.get('approved_hash') and not bud_store.changed_since_approved(old, ''):
            # Approved before fingerprints were kept and not edited since: what is saved now is what the board
            # approved, so fingerprint that. From here on a change shows as "changed" even on the approval day.
            info['approved_hash'] = lines_hash(old_lines)
        if submit and not info['submitted']:
            info.update(submitted=date.today().isoformat(), submitted_by=who)
        pw = _programs_ws()
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
            lw.update(rows, f'A2:I{len(rows) + 1}', value_input_option='RAW')
        if lw.row_count > len(rows) + 1:
            lw.batch_clear([f'A{len(rows) + 2}:I{lw.row_count}'])
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


# Plan lines and registered events: a plan line carries the #DR code of the event it became.

STOP_WORDS = set("""the and for with day night test tune school event events rallycross rally cross autocross solo
    road racing scca drscca region budget form pdf xlsm season january february march april may june july august
    september october november december jan feb mar apr jun jul aug sep sept oct nov dec""".split())


def _words(s):
    return {w for w in re.findall(r'[a-z]+', (s or '').lower()) if len(w) >= 3 and w not in STOP_WORDS}


def _code(e):
    return e.get('custom_tag') or e.get('unique_code') or ''


def match_score(item, month, event):
    """How well a plan line fits a registered event: its code in the line, shared distinctive words, the same month."""
    if _code(event) and _code(event).lower() in (item or '').lower():
        return 100
    score = 3 * len(_words(item) & _words(event.get('event_name')))
    em = int(event['date'][5:7]) if re.match(r'\d{4}-\d{2}', event.get('date') or '') else None
    if month and em:
        score += 2 if month == em else 1 if abs(month - em) == 1 else 0
    return score


def suggest(event, lines):
    """The one open plan line that fits an event (a shared word or its code, and no other line as good), or None."""
    scored = sorted(((match_score(ln['item'], ln['month'], event), i) for i, ln in enumerate(lines)), reverse=True)
    if not scored or scored[0][0] < 3 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return lines[scored[0][1]]


def plan_matches(events, lines):
    """For the nightly sync: ([(event, line)] to link, [(event, [candidate lines])] to ask about).

    Only open lines (no event yet, or an event that no longer exists) and events no line points to. A link needs
    a shared word and the same month (or the code in the line) and must be each other's best fit; an event that
    some open line partly fits but not clearly is returned to ask about. An event with no candidate at all was
    simply not in the plan.
    """
    codes = {_code(e) for e in events}
    linked = {ln['event_code'] for ln in lines if ln['event_code'] in codes}
    link, ask = [], []
    for e in events:
        y, prog = (e.get('date') or '')[:4], e.get('program_code')
        if not y or _code(e) in linked:
            continue
        cands = [ln for ln in lines if ln['year'] == y and ln['program'] == prog and ln['event_code'] not in codes
                 and ln not in [x for _, x in link]]
        best = suggest(e, cands)
        if best and match_score(best['item'], best['month'], e) >= 5:
            rivals = [x for x in events if x is not e and (x.get('date') or '')[:4] == y and x.get('program_code') == prog
                      and _code(x) not in linked]
            if all(match_score(best['item'], best['month'], x) < match_score(best['item'], best['month'], e) for x in rivals):
                link.append((e, best))
                continue
        near = [ln for ln in cands if match_score(ln['item'], ln['month'], e) >= 3]
        if near:
            ask.append((e, near))
    return link, ask


def open_lines(year, program, events):
    """A program's plan lines for a year that are not tied to a registered event yet."""
    codes = {_code(e) for e in events}
    return [ln for ln in saved_lines((plan(year).get(program) or {}).get('lines', [])) if ln['event_code'] not in codes]


def line_for(code):
    """The plan line an event code is tied to, or None."""
    return next((ln for ln in _snapshot()[0] if code and ln['event_code'] == code), None)


def link(year, program, sort, code):
    """Tie one plan line (year, program, sort) to an event code, untying any other line that had that code."""
    with FileLock(SAVE_LOCK), _lock:
        ws = _ws('Plan')
        values = ws.get('A:H', value_render_option='UNFORMATTED_VALUE')
        updates, found = [], False
        for n, r in enumerate(values[1:], start=2):
            r = list(r) + [''] * 8
            if str(r[0]) == str(year) and str(r[1]) == program and _num(r[7]) == float(sort):
                updates.append({'range': f'F{n}', 'values': [[code]]})
                found = True
            elif code and str(r[5]) == code:
                updates.append({'range': f'F{n}', 'values': [['']]})
        if not found:
            return False
        ws.batch_update(updates, value_input_option='RAW')
        refresh()
        return True


def placeholder(item):
    """A stand-in line from an old plan ("2026-01-RX Sample Event 5", "Event 12"), not worth copying forward."""
    return bool(re.search(r'\bsample event\b', item or '', re.I) or re.fullmatch(r'(\d{4}-\d{2}-\w+\s+)?event\s*\d+', (item or '').strip(), re.I))


def clean_name(item, year=None):
    """A plan line name in the plain style: no "2026-01-RX " numbering, no "#DR" event code, and without the year
    (the QuickBooks project name adds year, program and code itself). "June 2026 Sunday Funday (2026 RX #5)" with
    year 2026 becomes "June Sunday Funday (RX #5)"."""
    s = re.sub(r'^\d{4}-\d{2}-\w+\s+', '', item or '')
    s = re.sub(r'\s*\(?#DR\w+\)?', '', s)
    if year:
        s = re.sub(rf'\b{year}\b\s*', '', s)
    s = re.sub(r'\(\s*\)', '', s)
    s = re.sub(r'\s{2,}', ' ', s).strip(' :-')
    return s or (item or '').strip()


def outstanding():
    """Checks the bank has not cleared yet, as of the last sync: [{date, number, payee, amount, as_of}], oldest first."""
    hit, ver = _cache.get('outstanding'), C.version(os.environ['PLAN_SHEET_ID'])      # the version first: a write during the read below must not look fresh
    if hit and hit[0] == ver:
        return hit[1]
    try:
        res = C.batch_get(os.environ['PLAN_SHEET_ID'], ["'QBO Outstanding'!A:E"], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        rows = _rows((res.get('valueRanges') or [{}])[0].get('values', []), ['date', 'number', 'payee', 'amount', 'as_of'])
    except (gspread.exceptions.WorksheetNotFound, gspread.exceptions.APIError):
        rows = []
    for r in rows:
        r['amount'], r['date'], r['number'] = _num(r['amount']) or 0.0, str(r['date']), str(r['number'])
    rows.sort(key=lambda r: r['date'])
    _cache['outstanding'] = (ver, rows)
    return rows


def _mirror(title, cols, nums):
    """A whole QBO mirror tab as rows (empty before the first sync that writes it), cached on the sheet version."""
    hit, ver = _cache.get(title), C.version(os.environ['PLAN_SHEET_ID'])      # the version first: a write during the read below must not look fresh
    if hit and hit[0] == ver:
        return hit[1]
    try:
        res = C.batch_get(os.environ['PLAN_SHEET_ID'], [f"'{title}'!A:{chr(64 + len(cols))}"], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        rows = _rows((res.get('valueRanges') or [{}])[0].get('values', []), cols)
    except (gspread.exceptions.WorksheetNotFound, gspread.exceptions.APIError):
        rows = []
    for r in rows:
        for k in cols:
            r[k] = (_num(r[k]) or 0.0) if k in nums else str(r[k])
    _cache[title] = (ver, rows)
    return rows


def balance_sheet():
    """Today's QuickBooks balance sheet as of the last sync, to two levels: [{depth, name, amount, as_of}]."""
    rows = _mirror('QBO Balance Sheet', ['depth', 'name', 'amount', 'as_of'], {'amount', 'depth'})
    for r in rows:
        r['depth'] = int(r['depth'])
    return rows


def receivables():
    """Invoices still owed in QuickBooks as of the last sync, oldest first."""
    return sorted(_mirror('QBO Receivables', ['number', 'date', 'due', 'customer', 'total', 'balance', 'as_of'], {'total', 'balance'}),
                  key=lambda r: r['date'])


def accounts(program, years):
    """The QuickBooks accounts a program's money has been booked to in these years: [(section, account)], income first."""
    seen = {}
    for y in years:
        for ln in (actuals(y)[0].get(program) or {}).get('lines', []):
            seen.setdefault(ln['account'], ln['section'])
    return sorted(((sec, acc) for acc, sec in seen.items()), key=lambda x: (x[0] != 'Income', x[1]))


def by_account(lines, actual):
    """Plan against actual per QuickBooks account: [{'account', 'plan', 'actual', 'items'}]. Plan is the sum of the
    lines booked to it (profit sign: income +, cost -); actual is QuickBooks' amount with the same sign. Lines with no
    account (a reserve, a purchase with no account yet) are listed under ''."""
    rows = {}
    for ln in lines:
        r = rows.setdefault(ln.get('account') or '', {'account': ln.get('account') or '', 'plan': 0.0, 'actual': None, 'items': []})
        r['plan'] = round(r['plan'] + (ln['net'] or 0), 2)
        r['items'].append(ln['item'])
    for a in (actual or {}).get('lines', []):
        r = rows.setdefault(a['account'], {'account': a['account'], 'plan': None, 'actual': None, 'items': []})
        r['actual'] = round((r['actual'] or 0) + (a['amount'] if a['section'] == 'Income' else -a['amount']), 2)
    return sorted(rows.values(), key=lambda r: (r['account'] == '', -(abs(r['plan'] or 0) + abs(r['actual'] or 0))))
