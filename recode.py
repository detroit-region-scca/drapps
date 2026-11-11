"""Changing an event's code (#DR...) after it has a budget. Everything else in the app finds an event by its code, so
the change is carried to every record that names it: the budget and its lines, next year's "last year" link, the
plan line, board ballots, funds requests, money received and invoices. The budget's Drive folder is renamed too.
Cells are changed only where they hold exactly the old code (or a ballot key ending in it); notes and other free
text are left as written. QuickBooks is not touched: it is read nightly and matches on whatever the code is then."""
import logging
import os
import re

import sheet_cache as C
from storage import DRIVE, sheets_client, worksheet

# (sheet setting, tabs) that can hold an event code
PLACES = [
    ('BUDGETS_SHEET_ID', ['Budgets', 'Lines']),
    ('PLAN_SHEET_ID', ['Plan', 'Ballots']),
    ('FUNDS_SHEET_ID', ['Requests', 'Money received']),
    ('INVOICES_SHEET_ID', ['Invoices']),
]


def _new_value(cell, old, new):
    if cell == old:
        return new
    if cell.startswith('budget:') and cell.endswith(':' + old):          # a ballot on the budget: budget:YYYY:#CODE
        return cell[:-len(old)] + new
    return None


def _a1(row, col):
    letters = ''
    while col:
        col, r = divmod(col - 1, 26)
        letters = chr(65 + r) + letters
    return f'{letters}{row}'


def move(old, new):
    """Point every record from the code old to new. Returns {'Tab': cells changed}."""
    done = {}
    for env, tabs in PLACES:
        sid = os.environ.get(env)
        if not sid:
            continue
        for tab in tabs:
            try:
                ws = worksheet(sid, tab)
            except Exception:          # a tab that does not exist yet holds nothing to change
                continue
            upd = [{'range': _a1(r, c), 'values': [[v]]}
                   for r, row in enumerate(ws.get_all_values(), 1) for c, cell in enumerate(row, 1)
                   if (v := _new_value(str(cell), old, new)) is not None]
            if upd:
                ws.batch_update(upd, value_input_option='RAW')
                done[tab] = len(upd)
        C.invalidate(sid)
    for mod in ('bud_store', 'plan_store', 'fr_store', 'inv_store', 'received_store'):
        try:
            __import__(mod).refresh()
        except Exception:
            pass
    return done


def rename_folder(url, old, new):
    """Swap the code in the name of the Drive folder at url. Quietly does nothing if it can't."""
    m = re.search(r'/folders/([\w-]+)', url or '')
    if not m:
        return
    try:
        s = sheets_client().http_client.session
        r = s.get(f'{DRIVE}/{m.group(1)}', params={'supportsAllDrives': 'true', 'fields': 'name'})
        r.raise_for_status()
        name = r.json().get('name', '')
        if old in name:
            s.patch(f'{DRIVE}/{m.group(1)}', params={'supportsAllDrives': 'true'},
                    json={'name': name.replace(old, new)}).raise_for_status()
    except Exception:
        logging.exception('Could not rename the Drive folder for %s', new)
