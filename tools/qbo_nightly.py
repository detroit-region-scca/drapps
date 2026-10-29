"""Nightly QuickBooks sync for the DRSCCA apps. Read only on the QuickBooks side.

1. Event project actuals      -> Event Budgets sheet, 'QBO Actuals' tab
2. Program actuals by year    -> Annual Plan sheet, 'QBO Programs' tab
3. Bank and reserve balances  -> Annual Plan sheet, 'QBO Status' tab, and the worker points ledger's QBO balance
4. Open funds requests that QuickBooks shows as paid are marked paid (check number or region card)
5. Registered events tied to the annual plan line they clearly match; unclear ones go to the exceptions
6. Exceptions for the treasurer -> Annual Plan sheet, 'QBO Exceptions' tab

Run on the machine with the QuickBooks connection (see qbo_export.mjs); the web app only reads what this writes.
Needs node, the QBO env (QUICKBOOKS_* from the qbo-mcp token store) and the app's .env. --dry-run prints only.
"""
import json
import os
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, '.env'))
os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'] = os.environ.get('GOOGLE_SA_KEY_HOST') or os.environ['GOOGLE_SERVICE_ACCOUNT_FILE']
import bud_store as B  # noqa: E402
import fr_store as F  # noqa: E402
import plan_store as P  # noqa: E402
import qbo_logic as Q  # noqa: E402
import wp_store as W  # noqa: E402
from utils import load_events, log_audit_event  # noqa: E402

WHO = 'QuickBooks sync'
DRY = '--dry-run' in sys.argv
RESERVE = 'Worker points reserve'


def main():
    data = json.loads(subprocess.run(['node', os.path.join(HERE, 'qbo_export.mjs')], check=True, capture_output=True, text=True).stdout)
    as_of = data['as_of']
    today = date.fromisoformat(as_of)
    say = []

    events_rows = [[p['code'], p['project'], ln['section'], ln['account'], ln['amount'], as_of] for p in data['projects'] for ln in p['lines']]
    program_rows = [[y['year'], Q.program_of(ln['account']), ln['section'], ln['account'], ln['amount'], as_of]
                    for y in data['programs'] for ln in y['lines']]
    status_rows = [[RESERVE if b['type'] != 'Bank' else b['account'], abs(b['balance']) if b['type'] != 'Bank' else b['balance'], as_of]
                   for b in data['balances']]
    reserve = next((r[1] for r in status_rows if r[0] == RESERVE), None)
    say.append(f"{len(data['projects'])} event projects, {len(program_rows)} program account lines, {len(data['payments'])} payments")

    reqs = F.requests() if F.enabled() else []
    for r, paid in Q.match_funds(reqs, data['payments']):
        say.append(f"{r['id']} paid: {paid['note']}")
        if not DRY:
            F.close(r['id'], 'Paid', paid_date=paid['paid_date'], paid_ref=paid['paid_ref'], paid_amount=paid['paid_amount'],
                    closed_by=WHO, closed_note=paid['note'])
            log_audit_event('FUNDS_PAID', WHO, {'id': r['id'], 'ref': paid['paid_ref'], 'amount': paid['paid_amount']})
    if not DRY:
        F.refresh()
        reqs = F.requests() if F.enabled() else []

    events = load_events()
    exc = Q.exceptions(reqs, data['payments'], events, data['projects'], today)
    if P.enabled():
        link, ask = P.plan_matches(events, P._snapshot()[0])
        for e, ln in link:
            say.append(f"plan: {P._code(e)} {e['event_name']} -> {ln['item']}")
            if not DRY:
                P.link(ln['year'], ln['program'], ln['sort'], P._code(e))
                log_audit_event('PLAN_LINKED', WHO, {'event_code': P._code(e), 'year': ln['year'], 'program': ln['program'], 'item': ln['item']})
        for e, near in ask:
            exc.append({'key': f"evplan:{P._code(e)}", 'kind': 'Event not tied to the annual plan', 'date': e['date'],
                        'name': e['event_name'], 'amount': None, 'link': f"{e['date'][:4]}/{e['program_code']}",
                        'detail': f"{P._code(e)} may be the plan line " + ' or '.join(f'"{x["item"]}"' for x in near[:3])})
    if W.enabled() and reserve is not None:
        before = W.check()
        if not DRY and (abs((before['qbo'] or 0) - reserve) > 0.005 or before['as_of'] != today):
            W.set_qbo_balance(reserve, today)
            if abs((before['qbo'] or 0) - reserve) > 0.005:
                log_audit_event('WP_QBO_BALANCE', WHO, {'qbo': f'{reserve:.2f}', 'as_of': as_of})
            W._invalidate()
        diff = round((W.check()['value'] or 0) - reserve, 2)
        if abs(diff) > 0.005:
            exc.append({'key': f'reserve:{as_of}:{diff}', 'kind': 'Worker points ledger and QuickBooks differ', 'date': as_of,
                        'name': RESERVE, 'amount': diff, 'detail': f'Ledger value minus the QuickBooks reserve is ${diff:,.2f}', 'link': ''})
    say.append(f"{len(exc)} exceptions; reserve {reserve}")

    if DRY:
        for e in exc:
            say.append(f"  {e['kind']}: {e['detail']}")
    else:
        bw = B.open_book(os.environ['BUDGETS_SHEET_ID'])
        try:
            ws = bw.worksheet('QBO Actuals')
        except Exception:
            ws = bw.add_worksheet('QBO Actuals', rows=len(events_rows) + 100, cols=6)
        if len(events_rows) + 1 > ws.row_count:
            ws.add_rows(len(events_rows) + 1 - ws.row_count + 100)
        # Write first, then clear the leftover rows below, so the tab is never empty mid-sync.
        ws.update([['event_code', 'project', 'section', 'account', 'amount', 'as_of']] + events_rows, 'A1', value_input_option='RAW')
        if ws.row_count > len(events_rows) + 1:
            ws.batch_clear([f'A{len(events_rows) + 2}:F{ws.row_count}'])
        P.write_tab('QBO Programs', ['year', 'program', 'section', 'account', 'amount', 'as_of'], program_rows)
        P.write_tab('QBO Status', ['item', 'amount', 'as_of'], status_rows)
        P.write_tab('QBO Exceptions', P.EXC_COLS, [[e[c] if c != 'as_of' else as_of for c in P.EXC_COLS] for e in exc])
    print(f"QuickBooks sync {as_of}{' (dry run)' if DRY else ''}: " + '\n'.join(say))


if __name__ == '__main__':
    main()
