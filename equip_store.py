"""Reads and writes the Operations records: the DRSCCA Operations spreadsheet (OPERATIONS_SHEET_ID) in the DRSCCA
Apps shared drive, Operations folder. Plain tab names and column headers, so the region can keep it up by hand.

  Property                     one row per asset or group of like items (a vehicle, a trailer, 14 loaner helmets)
  Storage                      where things are kept, what it costs, whose name the account is in, how to get in
  Recurring costs and to-dos   what has to be done or paid and when: renewals, registrations, inspections, subscriptions.
                               A repeating one with a cost is a recurring cost; one charged automatically (yes) is
                               counted in the costs but kept off the to-do list.

Only equipment committee members signed in through an emailed link can change anything in the app (see equip.py).
"""
import os
import threading
import time
from datetime import date

from filelock import FileLock

from storage import DATA_DIR, open_book, worksheet

ITEM_COLS = ['id', 'name', 'program', 'category', 'place', 'year', 'quantity', 'cost', 'life_years', 'vin', 'plate',
             'condition', 'status', 'notes', 'docs_url', 'updated', 'updated_by', 'replace_cost', 'replace_year']
PLACE_COLS = ['id', 'name', 'address', 'cost_month', 'cost_year', 'account_holder', 'access', 'notes', 'updated', 'updated_by']
TASK_COLS = ['id', 'task', 'about', 'due', 'repeat_months', 'cost', 'owner', 'notes', 'last_done', 'done_by', 'category', 'paid_how', 'auto']
COST_CATEGORIES = ['Insurance', 'Storage', 'Vehicles', 'Safety', 'Software and web', 'Administration', 'Travel', 'Other']
TABS = {'Property': ITEM_COLS, 'Storage': PLACE_COLS, 'Recurring costs and to-dos': TASK_COLS}
# What people see as the header row (the app reads and writes by column position).
LABELS = {
    'Property': ['ID', 'Name', 'Program', 'Category', 'Kept at', 'Year bought', 'Quantity', 'Cost when bought', 'Useful life (years)',
                 'VIN or serial', 'Plate', 'Condition', 'Status', 'Notes', 'Documents link', 'Last updated', 'Updated by',
                 'Replacement cost estimate', 'Replace or buy in (year)'],
    'Storage': ['ID', 'Name', 'Address', 'Cost per month', 'Cost per year', 'Account in the name of / paid how', 'Access', 'Notes',
                'Last updated', 'Updated by'],
    'Recurring costs and to-dos': ['ID', 'What', 'For', 'Next due', 'Repeats every (months; blank = once)', 'Cost each time', 'Who',
                                   'Notes', 'Last done', 'Done by', 'Cost category', 'Paid how', 'Charged automatically (yes or blank)'],
}
NUMERIC = {'year', 'quantity', 'cost', 'life_years', 'cost_month', 'cost_year', 'repeat_months', 'replace_cost', 'replace_year'}
CATEGORIES = ['Vehicle', 'Trailer', 'Timing', 'Safety', 'Course', 'Hospitality', 'Promotion', 'Tools', 'Other']
STATUSES = ['In use', 'Needs repair', 'Planned', 'Retire', 'Gone']
CAPITAL = 2500.0        # the tax preparer's suggested threshold for a long-lived asset (May 2026)
CACHE_SECONDS = 15
LOCK = os.path.join(DATA_DIR, 'equipment.lock')

_lock = threading.Lock()
_cache = {}


def enabled():
    return bool(os.environ.get('OPERATIONS_SHEET_ID')) and os.environ.get('STORAGE_BACKEND') == 'sheets'


def _ws(title):
    return worksheet(os.environ['OPERATIONS_SHEET_ID'], title)


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
    res = open_book(os.environ['OPERATIONS_SHEET_ID']).values_batch_get([f"'{t}'!A:Z" for t in TABS],
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
    return _snapshot()['Property']


def places():
    return _snapshot()['Storage']


def tasks():
    return sorted(_snapshot()['Recurring costs and to-dos'], key=lambda t: t['due'] or '9999')


def _cell(v):
    return '' if v is None else v if isinstance(v, (int, float)) else str(v)


def save(tab, row):
    """Add the row (no id yet) or replace the row with its id. Returns the id."""
    cols = TABS[tab]
    with FileLock(LOCK), _lock:
        ws = _ws(tab)
        ids = ws.col_values(1)
        if not row.get('id'):
            prefix = {'Property': 'EQ', 'Storage': 'PL', 'Recurring costs and to-dos': 'TK'}[tab]
            nums = [int(v[len(prefix) + 1:]) for v in ids if v.startswith(prefix + '-') and v[len(prefix) + 1:].isdigit()]
            row = dict(row, id=f'{prefix}-{max(nums, default=0) + 1:03d}')
            ws.append_row([_cell(row.get(c)) for c in cols], value_input_option='RAW')
        else:
            n = ids.index(row['id']) + 1
            ws.update([[_cell(row.get(c)) for c in cols]], f'A{n}', value_input_option='RAW')
        refresh()
        return row['id']


def create_tabs():
    """Make the tabs with their plain headers if they are not there yet, and bring older headers up to date."""
    book = open_book(os.environ['OPERATIONS_SHEET_ID'])
    have = {w.title: w for w in book.worksheets()}
    for title, labels in LABELS.items():
        if title not in have:
            book.add_worksheet(title, rows=300, cols=len(labels)).update([labels], 'A1', value_input_option='RAW')
        elif have[title].row_values(1) != labels:
            if have[title].col_count < len(labels):
                have[title].add_cols(len(labels) - have[title].col_count)
            have[title].update([labels], 'A1', value_input_option='RAW')
    refresh()


# What the pages show, worked out from the rows. No I/O below.

def is_capital(it):
    return (it['cost'] or 0) >= CAPITAL


def lifecycle(it, today=None):
    """Straight-line view for planning (no tax use): yearly depreciation, value left now, year to replace.
    A replacement year set on the item wins over year bought + useful life."""
    today = today or date.today()
    cost, life, year = it['cost'], it['life_years'], it['year']
    out = None
    if cost and life and year:
        used = max(0, min(life, today.year - int(year)))
        out = {'per_year': round(cost / life, 2), 'left': round(cost * (1 - used / life), 2), 'replace': int(year + life)}
    if it.get('replace_year'):
        out = dict(out or {'per_year': None, 'left': None}, replace=int(it['replace_year']))
    return out


def replace_cost(it):
    """What replacing it will cost: the estimate when there is one, else what it cost."""
    return it.get('replace_cost') or it['cost'] or 0


def reserve_plan(rows, today=None, near_years=2, big=20000.0, big_within=5):
    """How to pay for replacements and planned purchases.

    near:  due this year or next: too close to save for, so budget them outright in that year's plan.
    later: due after that: set money aside each month, item by item, so each is paid for by July of its year
           (cost / months left). The monthly total is front-loaded when big items come due soon, as it should be.
    big:   later items due within big_within years that cost at least `big`: the board could fund these from
           savings instead; monthly_without_big is what is left to set aside then.
    """
    today = today or date.today()
    # From October on, plans are for next year: start saving next January.
    start = date(today.year + 1, 1, 1) if today.month >= 10 else date(today.year, today.month, 1)
    near, later = [], []
    for it in rows:
        lc = lifecycle(it, today)
        if it['status'] == 'Gone' or not lc or lc['replace'] < start.year or not replace_cost(it):
            continue
        row = {'it': it, 'year': lc['replace'], 'cost': replace_cost(it)}
        if lc['replace'] < start.year + near_years:
            near.append(row)
        else:
            months = (lc['replace'] - start.year) * 12 + 6 - (start.month - 1)
            row['monthly'] = round(row['cost'] / max(months, 1), 2)
            row['big'] = row['cost'] >= big and lc['replace'] < start.year + big_within
            later.append(row)
    near.sort(key=lambda r: (r['year'], -r['cost']))
    later.sort(key=lambda r: (r['year'], -r['cost']))
    monthly = round(sum(r['monthly'] for r in later), 2)
    return {'start': start.isoformat()[:7], 'start_label': start.strftime('%B %Y'), 'near': near, 'later': later, 'monthly': monthly,
            'big_total': round(sum(r['cost'] for r in later if r['big']), 2),
            'monthly_without_big': round(sum(r['monthly'] for r in later if not r['big']), 2)}


def replacement_plan(rows, today=None, horizon=20):
    """{year: [items due]} for the overview, and the monthly set-aside from reserve_plan as a yearly figure."""
    rp = reserve_plan(rows, today)
    plan = {}
    for r in rp['near'] + rp['later']:
        plan.setdefault(r['year'], []).append(r['it'])
    return dict(sorted(plan.items())), round(rp['monthly'] * 12, 2)


def next_due(t):
    """After a task is done: the same day repeat_months later (or '' for a one-time task)."""
    if not t['repeat_months'] or not t['due']:
        return ''
    y, m, d = map(int, t['due'][:10].split('-'))
    m += int(t['repeat_months'])
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f'{y}-{m:02d}-{min(d, 28):02d}'


def is_auto(t):
    return str(t.get('auto', '')).strip().lower() in ('yes', 'y', 'true', '1')


def recurring_costs(places_rows, tasks_rows):
    """Everything paid on a schedule, one row each: storage places and repeating tasks with a cost.
    [{'what', 'category', 'every', 'per_year', 'next', 'paid_how', 'owner', 'kind', 'id'}], biggest first."""
    rows = []
    for p in places_rows:
        per = (p['cost_month'] or 0) * 12 + (p['cost_year'] or 0)
        if per:
            rows.append({'what': p['name'], 'category': 'Storage', 'every': 1 if p['cost_month'] else 12, 'per_year': round(per, 2),
                         'next': '', 'paid_how': p['account_holder'], 'owner': '', 'kind': 'place', 'id': p['id']})
    for t in tasks_rows:
        if t['repeat_months'] and t['cost']:
            rows.append({'what': t['task'], 'category': t.get('category') or 'Other', 'every': t['repeat_months'],
                         'per_year': round(t['cost'] * 12 / t['repeat_months'], 2), 'next': t['due'], 'paid_how': t.get('paid_how', ''),
                         'owner': t['owner'], 'kind': 'task', 'id': t['id']})
    return sorted(rows, key=lambda r: -r['per_year'])


def fixed_cost_year(places_rows, tasks_rows):
    """What the region pays every year whether or not anything is used: storage plus repeating tasks (insurance,
    registrations, inspections), each repeating task counted per year."""
    storage = sum((p['cost_month'] or 0) * 12 + (p['cost_year'] or 0) for p in places_rows)
    recurring = sum((t['cost'] or 0) * 12 / t['repeat_months'] for t in tasks_rows if t['repeat_months'])
    return round(storage, 2), round(recurring, 2)
