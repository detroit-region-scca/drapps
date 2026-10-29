"""Reads and writes the region's equipment records: three tabs on the Registry sheet (REGISTRY_SHEET_ID).

  Equipment Items   one row per asset or group of like items (a vehicle, a trailer, 14 loaner helmets, pylons)
  Equipment Places  where things are kept, what it costs, whose name the account is in, how to get in
  Equipment Tasks   what has to be done and when (insurance renewal, registrations, inspections), who does it

The sheet stays usable by hand. Only equipment committee members signed in through an emailed link can change
anything in the app (see equip.py).
"""
import os
import threading
import time
from datetime import date

from filelock import FileLock

from storage import DATA_DIR, open_book, worksheet

ITEM_COLS = ['id', 'name', 'program', 'category', 'place', 'year', 'quantity', 'cost', 'life_years', 'vin', 'plate',
             'condition', 'status', 'notes', 'docs_url', 'updated', 'updated_by']
PLACE_COLS = ['id', 'name', 'address', 'cost_month', 'cost_year', 'account_holder', 'access', 'notes', 'updated', 'updated_by']
TASK_COLS = ['id', 'task', 'about', 'due', 'repeat_months', 'cost', 'owner', 'notes', 'last_done', 'done_by']
TABS = {'Equipment Items': ITEM_COLS, 'Equipment Places': PLACE_COLS, 'Equipment Tasks': TASK_COLS}
NUMERIC = {'year', 'quantity', 'cost', 'life_years', 'cost_month', 'cost_year', 'repeat_months'}
CATEGORIES = ['Vehicle', 'Trailer', 'Timing', 'Safety', 'Course', 'Hospitality', 'Promotion', 'Tools', 'Other']
STATUSES = ['In use', 'Needs repair', 'Retire', 'Gone']
CAPITAL = 2500.0        # the tax preparer's suggested threshold for a long-lived asset (May 2026)
CACHE_SECONDS = 15
LOCK = os.path.join(DATA_DIR, 'equipment.lock')

_lock = threading.Lock()
_cache = {}


def enabled():
    return bool(os.environ.get('REGISTRY_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _ws(title):
    return worksheet(os.environ['REGISTRY_SHEET_ID'], title)


def refresh():
    _cache.clear()


def _num(v):
    try:
        return float(v) if str(v).strip() != '' else None
    except (TypeError, ValueError):
        return None


def _snapshot():
    hit = _cache.get('snap')
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    res = open_book(os.environ['REGISTRY_SHEET_ID']).values_batch_get([f"'{t}'!A:Q" for t in TABS],
                                                                     params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    snap = {}
    for (title, cols), vr in zip(TABS.items(), res.get('valueRanges', [])):
        rows = [dict(zip(cols, list(r) + [''] * len(cols))) for r in vr.get('values', [])[1:] if r and str(r[0]).strip()]
        for r in rows:
            for k in cols:
                r[k] = _num(r[k]) if k in NUMERIC else str(r[k])
        snap[title] = rows
    _cache['snap'] = (time.time(), snap)
    return snap


def items():
    return _snapshot()['Equipment Items']


def places():
    return _snapshot()['Equipment Places']


def tasks():
    return sorted(_snapshot()['Equipment Tasks'], key=lambda t: t['due'] or '9999')


def _cell(v):
    return '' if v is None else v if isinstance(v, (int, float)) else str(v)


def save(tab, row):
    """Add the row (no id yet) or replace the row with its id. Returns the id."""
    cols = TABS[tab]
    with FileLock(LOCK), _lock:
        ws = _ws(tab)
        ids = ws.col_values(1)
        if not row.get('id'):
            prefix = {'Equipment Items': 'EQ', 'Equipment Places': 'PL', 'Equipment Tasks': 'TK'}[tab]
            nums = [int(v[len(prefix) + 1:]) for v in ids if v.startswith(prefix + '-') and v[len(prefix) + 1:].isdigit()]
            row = dict(row, id=f'{prefix}-{max(nums, default=0) + 1:03d}')
            ws.append_row([_cell(row.get(c)) for c in cols], value_input_option='RAW')
        else:
            n = ids.index(row['id']) + 1
            ws.update([[_cell(row.get(c)) for c in cols]], f'A{n}', value_input_option='RAW')
        refresh()
        return row['id']


def create_tabs():
    """Make the three tabs with their headers if they are not there yet (first run)."""
    book = open_book(os.environ['REGISTRY_SHEET_ID'])
    have = {w.title for w in book.worksheets()}
    for title, cols in TABS.items():
        if title not in have:
            book.add_worksheet(title, rows=300, cols=len(cols)).update([cols], 'A1', value_input_option='RAW')
    refresh()


# What the pages show, worked out from the rows. No I/O below.

def is_capital(it):
    return (it['cost'] or 0) >= CAPITAL


def lifecycle(it, today=None):
    """Straight-line view for planning (no tax use): yearly depreciation, value left now, year to replace."""
    today = today or date.today()
    cost, life, year = it['cost'], it['life_years'], it['year']
    if not (cost and life and year):
        return None
    used = max(0, min(life, today.year - int(year)))
    return {'per_year': round(cost / life, 2), 'left': round(cost * (1 - used / life), 2), 'replace': int(year + life)}


def replacement_plan(rows, today=None, horizon=20):
    """{year: [items due for replacement]} for this year onward, plus the even yearly reserve that covers them."""
    today = today or date.today()
    plan = {}
    for it in rows:
        lc = lifecycle(it, today)
        if lc and it['status'] != 'Gone' and today.year <= lc['replace'] <= today.year + horizon:
            plan.setdefault(lc['replace'], []).append(it)
    total = sum(it['cost'] or 0 for v in plan.values() for it in v)
    years = (max(plan) - today.year + 1) if plan else 1
    return dict(sorted(plan.items())), round(total / years, 2) if plan else 0.0


def next_due(t):
    """After a task is done: the same day repeat_months later (or '' for a one-time task)."""
    if not t['repeat_months'] or not t['due']:
        return ''
    y, m, d = map(int, t['due'][:10].split('-'))
    m += int(t['repeat_months'])
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f'{y}-{m:02d}-{min(d, 28):02d}'


def fixed_cost_year(places_rows, tasks_rows):
    """What the region pays every year whether or not anything is used: storage plus repeating tasks (insurance,
    registrations, inspections), each repeating task counted per year."""
    storage = sum((p['cost_month'] or 0) * 12 + (p['cost_year'] or 0) for p in places_rows)
    recurring = sum((t['cost'] or 0) * 12 / t['repeat_months'] for t in tasks_rows if t['repeat_months'])
    return round(storage, 2), round(recurring, 2)
