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
from datetime import date

from filelock import FileLock

from storage import DATA_DIR, open_book, worksheet
import sheet_cache as C
from utils import to_number

ITEM_COLS = ['id', 'name', 'program', 'category', 'place', 'year', 'quantity', 'cost', 'life_years', 'vin', 'plate',
             'condition', 'status', 'notes', 'docs_url', 'updated', 'updated_by', 'replace_cost', 'replace_year', 'nickname', 'seen', 'account']
PLACE_COLS = ['id', 'name', 'address', 'cost_month', 'cost_year', 'account_holder', 'access', 'notes', 'updated', 'updated_by', 'qbo_check', 'status']
TASK_COLS = ['id', 'task', 'about', 'due', 'repeat_months', 'cost', 'owner', 'notes', 'last_done', 'done_by', 'category', 'paid_how', 'auto',
             'qbo_check', 'account', 'ends']
# QuickBooks accounts Operations money is booked to; a bill or item on any other account (a program's) is not Operations'.
ACCOUNTS = ['Operations Professional Services', 'Operations Storage', 'Operations Repair & Maintenance', 'Operations Supplies',
            'Operations Travel', 'Operations Miscellaneous', 'Capital Equipment:Race Equipment',
            'Capital Equipment:Rallycross Equipment', 'Capital Equipment:Solo Equipment', 'Capital Equipment:Equipment in Storage']
STORAGE_ACCOUNT = 'Operations Storage'
# How often, as people say it (the sheet keeps months; blank = once).
FREQUENCIES = [('', 'Once'), (1, 'Monthly'), (3, 'Every 3 months'), (6, 'Every 6 months'), (12, 'Yearly'), (24, 'Every 2 years'),
               (36, 'Every 3 years')]
COST_CATEGORIES = ['Insurance', 'Storage', 'Vehicles', 'Safety', 'Software and web', 'Administration', 'Travel', 'Other']
DOC_COLS = ['id', 'for_id', 'for_name', 'folder', 'needed', 'notes']
TABS = {'Property': ITEM_COLS, 'Storage': PLACE_COLS, 'Recurring costs and to-dos': TASK_COLS, 'Documents': DOC_COLS}
# What people see as the header row (the app reads and writes by column position).
LABELS = {
    'Property': ['ID', 'Name', 'Program', 'Category', 'Kept at', 'Year bought', 'Quantity', 'Cost when bought', 'Useful life (years)',
                 'VIN or serial', 'Plate', 'Condition', 'Status', 'Notes', 'Documents link', 'Last updated', 'Updated by',
                 'Replacement cost estimate', 'Replace or buy in (year)', 'Also called (for things other items are kept in)',
                 'Location last confirmed', 'QuickBooks account (for equipment to buy)'],
    'Storage': ['ID', 'Name', 'Address', 'Cost per month', 'Cost per year', 'Account in the name of / paid how', 'Access', 'Notes',
                'Last updated', 'Updated by', 'Checked against QuickBooks', 'Status (blank = in use; Closed = no longer used)'],
    'Recurring costs and to-dos': ['ID', 'What', 'For', 'Next due', 'Repeats every (months; blank = once)', 'Cost each time', 'Who',
                                   'Notes', 'Last done', 'Done by', 'Cost category', 'Paid how', 'Charged automatically (yes or blank)',
                                   'Checked against QuickBooks', 'QuickBooks account',
                                   'Ends (last charge date; blank = keeps going)'],
    'Documents': ['ID', 'Item or place ID', 'For', 'Drive folder', 'Documents needed (comma list; file names start with these words)', 'Notes'],
}
NUMERIC = {'year', 'quantity', 'cost', 'life_years', 'cost_month', 'cost_year', 'repeat_months', 'replace_cost', 'replace_year'}
CATEGORIES = ['Vehicle', 'Trailer', 'Timing', 'Safety', 'Course', 'Hospitality', 'Promotion', 'Tools', 'Other']
STATUSES = ['In use', 'Needs repair', 'Planned', 'Retire', 'Gone']
CAPITAL = 2500.0        # the tax preparer's suggested threshold for a long-lived asset (May 2026)
# The replacement plan's rules, kept on their own 'Plan settings' tab so Operations can change them in the app (or the sheet).
PLAN_TAB = 'Plan settings'
PLAN_LABELS = ['Setting', 'Value', 'What it means', 'Last updated', 'Updated by']
PLAN_DEFAULTS = {'big_cost': 20000.0, 'big_within_years': 5, 'budget_within_years': 2, 'big_from_savings': True}
PLAN_HELP = {
    'big_cost': 'A replacement costing at least this much is a big item.',
    'big_within_years': 'Big items due within this many years can be paid from savings instead of saved for monthly.',
    'budget_within_years': 'Items due within this many years (1 = this year only, 2 = this year or next) are bought outright in that year\'s budget.',
    'big_from_savings': 'yes: offer paying the big items from savings. no: save monthly for everything.',
}
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
    C.invalidate(os.environ['OPERATIONS_SHEET_ID'])


def _num(v):
    try:
        return to_number(v) if str(v).strip() != '' else None
    except (TypeError, ValueError):
        return None


def _snapshot():
    hit = _cache.get('snap')
    if hit and hit[0] == C.version(os.environ['OPERATIONS_SHEET_ID']):
        return hit[1]
    res = C.batch_get(os.environ['OPERATIONS_SHEET_ID'], [f"'{t}'!A:Z" for t in TABS],
                                                                     params={'valueRenderOption': 'UNFORMATTED_VALUE'})
    snap = {}
    for (title, cols), vr in zip(TABS.items(), res.get('valueRanges', [])):
        rows = [dict(zip(cols, list(r) + [''] * len(cols))) for r in vr.get('values', [])[1:] if r and str(r[0]).strip()]
        for r in rows:
            for k in cols:
                r[k] = _num(r[k]) if k in NUMERIC else str(r[k])
        snap[title] = rows
    _cache['snap'] = (C.version(os.environ['OPERATIONS_SHEET_ID']), snap)
    return snap


def items():
    return _snapshot()['Property']


def places(closed=False):
    """Storage places in use (closed=True: also the ones the region no longer uses)."""
    return [p for p in _snapshot()['Storage'] if closed or p.get('status', '') != 'Closed']


def tasks():
    return sorted(_snapshot()['Recurring costs and to-dos'], key=lambda t: t['due'] or '9999')


def documents():
    return _snapshot()['Documents']


def _plan_cfg(rows):
    """The plan settings from the tab's rows ([key, value, ...]), anything missing or unreadable left at its default."""
    cfg = dict(PLAN_DEFAULTS)
    for r in rows:
        if not r or len(r) < 2:
            continue
        k, v = str(r[0]).strip(), str(r[1]).strip()
        if k not in PLAN_DEFAULTS or v == '':
            continue
        if k == 'big_from_savings':
            cfg[k] = v.lower() not in ('no', 'n', 'false', '0', 'off')
            continue
        n = _num(v.replace('$', '').replace(',', ''))
        if n is not None and n >= 0:
            cfg[k] = int(n) if k.endswith('_years') else float(n)
    cfg['budget_within_years'] = max(1, cfg['budget_within_years'])
    return cfg


def plan_settings():
    """The replacement plan's rules (PLAN_DEFAULTS until someone saves them)."""
    sid = os.environ['OPERATIONS_SHEET_ID']
    hit = _cache.get('plan')
    if hit and hit[0] == C.version(sid):
        return hit[1]
    try:
        res = C.batch_get(sid, [f"'{PLAN_TAB}'!A:B"], params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        rows = (res.get('valueRanges') or [{}])[0].get('values', [])[1:]
    except Exception:            # no tab yet: the defaults
        rows = []
    cfg = _plan_cfg(rows)
    _cache['plan'] = (C.version(sid), cfg)
    return cfg


def save_plan_settings(cfg, who, when):
    """Write the four settings to the 'Plan settings' tab (made the first time), one row each, with who and when."""
    with FileLock(LOCK), _lock:
        book = open_book(os.environ['OPERATIONS_SHEET_ID'])
        have = {w.title: w for w in book.worksheets()}
        ws = have.get(PLAN_TAB) or book.add_worksheet(PLAN_TAB, rows=20, cols=len(PLAN_LABELS))
        rows = [PLAN_LABELS] + [[k, ('yes' if cfg[k] else 'no') if k == 'big_from_savings' else cfg[k], PLAN_HELP[k], when, who]
                                for k in PLAN_DEFAULTS]
        ws.update(rows, 'A1', value_input_option='RAW')
        refresh()


def _cell(v):
    return '' if v is None else v if isinstance(v, (int, float)) else str(v)


def save(tab, row, changed=None):
    """Add the row (no id yet) or update the row with its id. Returns the id.

    changed: the fields this save means to change. With it, an existing row is re-read from the sheet first and only
    those fields are written over it, so an edit someone made straight in the sheet meanwhile survives. Without it
    the whole row is written (only for callers that hold the full, current row)."""
    cols = TABS[tab]
    with FileLock(LOCK), _lock:
        ws = _ws(tab)
        ids = ws.col_values(1)
        if not row.get('id'):
            prefix = {'Property': 'EQ', 'Storage': 'PL', 'Recurring costs and to-dos': 'TK', 'Documents': 'DC'}[tab]
            nums = [int(v[len(prefix) + 1:]) for v in ids if v.startswith(prefix + '-') and v[len(prefix) + 1:].isdigit()]
            row = dict(row, id=f'{prefix}-{max(nums, default=0) + 1:03d}')
            ws.append_row([_cell(row.get(c)) for c in cols], value_input_option='RAW')
        else:
            n = ids.index(row['id']) + 1
            out = row
            if changed is not None:
                end = chr(64 + len(cols)) if len(cols) <= 26 else 'Z'
                live = (ws.get(f'A{n}:{end}{n}', value_render_option='UNFORMATTED_VALUE') or [[]])[0]
                out = dict(zip(cols, list(live) + [''] * len(cols)))
                out.update({k: row.get(k) for k in changed if k in cols and k != 'id'})
            ws.update([[_cell(out.get(c)) for c in cols]], f'A{n}', value_input_option='RAW')
        refresh()
        return row['id']


def norm(v):
    """A value as the forms show it, for telling whether a field was really changed ("1,200" == 1200 == "1200.0")."""
    s = '' if v is None else str(v).strip().replace('$', '').replace(',', '')
    try:
        f = float(s)
        return f'{f:g}'
    except ValueError:
        return s


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


def reserve_plan(rows, today=None, cfg=None):
    """How to pay for replacements and planned purchases.

    near:  due this year or next: too close to save for, so budget them outright in that year's plan.
    later: due after that: set money aside each month, item by item, so each is paid for by July of its year
           (cost / months left). The monthly total is front-loaded when big items come due soon, as it should be.
    big:   later items due within big_within years that cost at least `big`: the board could fund these from
           savings instead; monthly_without_big is what is left to set aside then.
    cfg:   the rules (plan_settings(); PLAN_DEFAULTS when not given): budget_within_years, big_cost, big_within_years,
           big_from_savings (False: no big items, save monthly for everything).
    """
    cfg = dict(PLAN_DEFAULTS, **(cfg or {}))
    near_years, big, big_within = cfg['budget_within_years'], cfg['big_cost'], cfg['big_within_years']
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
            row['big'] = cfg['big_from_savings'] and row['cost'] >= big and lc['replace'] < start.year + big_within
            later.append(row)
    near.sort(key=lambda r: (r['year'], -r['cost']))
    later.sort(key=lambda r: (r['year'], -r['cost']))
    monthly = round(sum(r['monthly'] for r in later), 2)
    return {'start': start.isoformat()[:7], 'start_label': start.strftime('%B %Y'), 'near': near, 'later': later, 'monthly': monthly,
            'cfg': cfg, 'near_until': start.year + near_years - 1,
            'big_total': round(sum(r['cost'] for r in later if r['big']), 2),
            'monthly_without_big': round(sum(r['monthly'] for r in later if not r['big']), 2)}


def replacement_plan(rows, today=None, horizon=20, cfg=None):
    """{year: [items due]} for the overview, and the monthly set-aside from reserve_plan as a yearly figure."""
    rp = reserve_plan(rows, today, cfg)
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
                         'each': p['cost_month'] or p['cost_year'], 'next': '', 'paid_how': p['account_holder'], 'owner': '', 'kind': 'place',
                         'id': p['id'], 'qbo': p.get('qbo_check', ''), 'notes': p['notes'], 'account': STORAGE_ACCOUNT})
    for t in tasks_rows:
        if t['repeat_months'] and t['cost']:
            rows.append({'what': t['task'], 'category': t.get('category') or 'Other', 'every': t['repeat_months'],
                         'per_year': round(t['cost'] * 12 / t['repeat_months'], 2), 'each': t['cost'], 'next': t['due'],
                         'paid_how': t.get('paid_how', ''), 'owner': t['owner'], 'kind': 'task', 'id': t['id'], 'qbo': t.get('qbo_check', ''),
                         'notes': t['notes'], 'account': t.get('account', ''), 'ends': t.get('ends', '')})
    return sorted(rows, key=lambda r: -r['per_year'])


def locations(items_rows, places_rows):
    """Where everything is. Each item's "Kept at" is a storage place, another item (a trailer or motorhome, by name or
    "also called"), "With <person>", or free text. Returns (places, people, other, unknown):
      places  [(place, [entries])]   entries are {'it', 'inside': [entries]} so trailer contents nest under the trailer
      people  [(person, [entries])]
      other   [(text, [entries])]    kept somewhere that is not a known place or item
      unknown [entries]              no location at all
    """
    live = [i for i in items_rows if i['status'] not in ('Gone', 'Planned')]      # planned purchases are nowhere yet
    by_name = {}
    for i in live:
        for n in (i['name'], i.get('nickname')):
            if n:
                by_name[n.strip().lower()] = i
    pl = {p['name'].strip().lower(): p for p in places_rows}

    def resolve(text):
        t = (text or '').strip()
        low = t.lower()
        if not t:
            return 'unknown', None
        if low.startswith('with '):
            return 'person', t[5:].strip()
        if low in pl:
            return 'place', pl[low]
        for k, p in pl.items():
            if k.startswith(low) or low.startswith(k):
                return 'place', p
        if low in by_name:
            return 'in', by_name[low]
        return 'other', t

    where = {i['id']: resolve(i['place']) for i in live}
    kids = {}
    for i in live:
        kind, target = where[i['id']]
        if kind == 'in' and target['id'] != i['id']:
            kids.setdefault(target['id'], []).append(i)

    def entry(i, depth=0):
        inside = sorted(kids.get(i['id'], []), key=lambda x: x['name'].lower()) if depth < 3 else []
        return {'it': i, 'inside': [entry(k, depth + 1) for k in inside]}

    def order(rows):      # vehicles and trailers first, then the rest by name
        return sorted(rows, key=lambda i: (i['category'] not in ('Vehicle', 'Trailer'), i['name'].lower()))

    places_out = [(p, [entry(i) for i in order([i for i in live if where[i['id']] == ('place', p)])]) for p in places_rows]
    people, other = {}, {}
    for i in order(live):
        kind, target = where[i['id']]
        if kind == 'person':
            people.setdefault(target, []).append(entry(i))
        elif kind == 'other':
            other.setdefault(target, []).append(entry(i))
    return (places_out, sorted(people.items()), sorted(other.items()),
            [entry(i) for i in order([i for i in live if where[i['id']][0] == 'unknown'])])


def year_cost(c, year):
    """What a bill (a recurring_costs row) costs in the given year: nothing once it has ended (its 'ends' date is
    before the year), only the months up to its end in the year it ends, and nothing for a bill paid yearly or less
    often whose next charge falls after the year (a new domain renewal first due the year after, say)."""
    y = str(year)
    ends, nxt, every = c.get('ends') or '', c.get('next') or '', c['every'] or 12
    if ends and ends[:4] < y:
        return 0.0
    if every >= 12 and nxt and nxt[:4] > y:
        return 0.0
    if ends and ends[:4] == y and every < 12:
        return round(c['each'] * (int(ends[5:7]) // every), 2)
    return c['per_year']


def budget(year, items_rows, places_rows, tasks_rows, today=None, cfg=None):
    """What the Operations records say a year costs, by QuickBooks account, for the Budget column of the Operations
    annual plan (the way an event budget fills it for a program): every bill on an Operations account for a full year,
    and equipment the replacement list buys that year on its Capital Equipment account. {account: {'amount': cost as a negative number, 'items': [(what, amount)]}}."""
    out = {}

    def add(acc, what, amount):
        r = out.setdefault(acc, {'amount': 0.0, 'items': []})
        r['amount'] = round(r['amount'] - amount, 2)
        r['items'].append((what, round(amount, 2)))
    for c in recurring_costs(places_rows, tasks_rows):
        if c['account'] in ACCOUNTS and (amount := year_cost(c, year)):
            add(c['account'], c['what'], amount)
    live = [i for i in items_rows if i['status'] != 'Gone']
    rp = reserve_plan(live, today, cfg)
    for r in rp['near']:
        if str(r['year']) == str(year):
            add(r['it'].get('account') or 'Capital Equipment (no account set)', r['it']['name'], r['cost'])
    # Money set aside for later replacements is not in the budget: it stays the region's money, in savings, and the
    # board decides it as its own motion (Board > Motions), linked from the replacement plan page.
    for r in out.values():
        r['items'].sort(key=lambda x: -x[1])
    return out


def money_word(v):
    return f"${v:,.0f}"


def every_label(months):
    m = {k: v for k, v in FREQUENCIES}
    return m.get(int(months) if months else '', f'Every {months:g} months')


def fixed_cost_year(places_rows, tasks_rows):
    """What the region pays every year whether or not anything is used: storage plus repeating tasks (insurance,
    registrations, inspections), each repeating task counted per year."""
    storage = sum((p['cost_month'] or 0) * 12 + (p['cost_year'] or 0) for p in places_rows)
    recurring = sum((t['cost'] or 0) * 12 / t['repeat_months'] for t in tasks_rows if t['repeat_months'])
    return round(storage, 2), round(recurring, 2)
