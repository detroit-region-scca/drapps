"""Reads and writes the DRSCCA Annual Plan Google Sheet (PLAN_SHEET_ID) in the DRSCCA Apps Shared Drive.

  Plan            one row per planned event or item: year, program, item, month, net (+ profit, - cost)
  Programs        one row per program and year: director, email, notes for the board, last update
  QBO Programs    the region profit and loss by account and year      } written nightly by
  QBO Status      bank balances and the worker points reserve          } tools/qbo_nightly.py,
  QBO Exceptions  things in the books the treasurer should look at     } read only here
  Reviewed        exception keys the treasurer has looked at, so they stay hidden
"""
import os
import re
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
    return [ln for ln in (plan(year).get(program) or {}).get('lines', []) if ln['event_code'] not in codes]


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
