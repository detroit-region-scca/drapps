"""Nightly QuickBooks sync for the DRSCCA apps.

1. Event project actuals      -> Event Budgets sheet, 'QBO Actuals' tab
2. Program actuals by year    -> Annual Plan sheet, 'QBO Programs' tab, and by month -> 'QBO Monthly' tab
3. Bank and reserve balances  -> Annual Plan sheet, 'QBO Status' tab, and the worker points ledger's QBO balance
4. Open funds requests that QuickBooks shows as paid are marked paid (check number or region card)
5. Registered events tied to the annual plan line they clearly match; unclear ones go to the exceptions
6. Exceptions for the treasurer -> Annual Plan sheet, 'QBO Exceptions' tab
7. The one QuickBooks write (tools/qbo_write.mjs): a paid request's receipts are attached to its check or card statement
8. Balance sheet and money owed -> Annual Plan sheet, 'QBO Balance Sheet' and 'QBO Receivables' tabs, for the report

Run on the machine with the QuickBooks connection (see qbo_export.mjs); the web app only reads what this writes.
Needs node, the QBO env (QUICKBOOKS_* from the qbo-mcp token store) and the app's .env. --dry-run prints only.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, '.env'))
os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'] = os.environ.get('GOOGLE_SA_KEY_HOST') or os.environ['GOOGLE_SERVICE_ACCOUNT_FILE']
import bud_store as B  # noqa: E402
from storage import open_book  # noqa: E402
import fr_store as F  # noqa: E402
import plan_store as P  # noqa: E402
import qbo_logic as Q  # noqa: E402
import members_store as W  # noqa: E402
import fr_logic as FL  # noqa: E402
from utils import load_events, log_audit_event, send_mail  # noqa: E402

WHO = 'QuickBooks sync'
DRY = '--dry-run' in sys.argv
MANUAL = '--manual' in sys.argv     # asked for with the Refresh button: skip the once-a-month jobs
RESERVE = 'Worker points reserve'


def main():
    data = json.loads(subprocess.run(['node', os.path.join(HERE, 'qbo_export.mjs')], check=True, capture_output=True, text=True).stdout)
    as_of = data['as_of']
    today = date.fromisoformat(as_of)
    say = []

    events_rows = [[p['code'], p['project'], ln['section'], ln['account'], ln['amount'], as_of] for p in data['projects'] for ln in p['lines']]
    program_rows = [[y['year'], Q.program_of(ln['account']), ln['section'], ln['account'], ln['amount'], as_of]
                    for y in data['programs'] for ln in y['lines']]
    monthly_rows = []
    for y in data.get('monthly', []):
        net = {}
        for ln in y['lines']:
            sign = 1 if ln['section'] == 'Income' else -1
            for m, v in enumerate(ln['months'], start=1):
                key = (Q.program_of(ln['account']), m)
                net[key] = net.get(key, 0) + sign * v
        monthly_rows += [[y['year'], prog, m, round(v, 2), as_of] for (prog, m), v in sorted(net.items()) if abs(v) >= 0.005]
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
            F.refresh()
            msg = FL.closed_message(F.get(r['id']) or {}, f"https://drapps.drscca.org/funds/{r['id']}")
            if msg:
                send_mail(*msg, r['submitter_email'])
    if not DRY:
        F.refresh()
        reqs = F.requests() if F.enabled() else []

    exc = []
    try:
        exc += qbo_writes(data, reqs, say)
    except Exception as e:      # the sync's reading and mirroring must go on whatever a write does
        say.append(f'QuickBooks writes failed: {e}')

    events = load_events()
    exc += Q.exceptions(reqs, data['payments'], events, data['projects'], today)
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
        value = round(sum(m['points'] for m in W.members()) * 5, 2)
        diff = round(value - reserve, 2)
        if abs(diff) > 0.005:
            exc.append({'key': f'reserve:{as_of}:{diff}', 'kind': 'Worker points and QuickBooks differ', 'date': as_of,
                        'name': RESERVE, 'amount': diff, 'detail': f'Worker points value minus the QuickBooks reserve is ${diff:,.2f}', 'link': ''})
    say.append(f"{len(exc)} exceptions; reserve {reserve}")

    if DRY:
        for e in exc:
            say.append(f"  {e['kind']}: {e['detail']}")
    else:
        bw = open_book(os.environ['BUDGETS_SHEET_ID'])
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
        P.write_tab('QBO Monthly', ['year', 'program', 'month', 'net', 'as_of'], monthly_rows)
        P.write_tab('QBO Status', ['item', 'amount', 'as_of'], status_rows)
        P.write_tab('QBO Exceptions', P.EXC_COLS, [[e[c] if c != 'as_of' else as_of for c in P.EXC_COLS] for e in exc])
        P.write_tab('QBO Balance Sheet', ['depth', 'name', 'amount', 'as_of'],
                    [[b['depth'], b['name'], b['amount'], as_of] for b in data.get('balance_sheet', [])])
        P.write_tab('QBO Receivables', ['number', 'date', 'due', 'customer', 'total', 'balance', 'as_of'],
                    [[r['number'], r['date'], r['due'], r['customer'], r['total'], r['balance'], as_of] for r in data.get('receivables', [])])
        used_deposits = set()
        try:
            import inv_store
            if inv_store.enabled():
                for inv, paid in Q.match_invoices(inv_store.invoices(), data.get('deposits', [])):
                    inv_store.update(inv['id'], status='Paid', closed_by=WHO, **paid)
                    log_audit_event('INVOICE_PAID', WHO, {'id': inv['id'], 'amount': paid['paid_amount'], 'ref': paid['paid_ref']})
                    say.append(f"invoice {inv['id']} paid by {paid['paid_ref']}")
                    used_deposits |= {d['id'] for d in data.get('deposits', []) if d['date'] == paid['paid_date']
                                      and abs(d['amount'] - paid['paid_amount']) < 0.005}
        except Exception as e:
            say.append(f"invoice matching failed: {e}")
        try:   # cash and checks reported received: deposited once QuickBooks shows a deposit of the same amount
            import received_store as RS
            if RS.enabled():
                for rep, dep in RS.match_deposits(RS.reports(), data.get('deposits', []), used=used_deposits):
                    RS.update(rep['id'], status='Deposited', closed_by=WHO, **dep)
                    log_audit_event('MONEY_DEPOSITED', WHO, {'id': rep['id'], 'amount': dep['deposit_amount'], 'ref': dep['deposit_ref']})
                    say.append(f"money received {rep['id']} deposited ({dep['deposit_ref']})")
        except Exception as e:
            say.append(f"money received matching failed: {e}")
        try:
            import officials
            say.append(f"officials copied to Drive: {officials.mirror_to_sheet()}")
        except Exception as e:      # a copy for people to read; never stop the sync over it
            say.append(f"officials copy failed: {e}")
        P.write_tab('QBO Outstanding', ['date', 'number', 'payee', 'amount', 'as_of'],
                    [[o['date'], o['number'], o['payee'], o['amount'], as_of] for o in data.get('outstanding', [])])
    try:   # on the 1st: email the treasurer the journal entry for worker points not yet reconciled (user asked, 2026-11-02)
        t0 = date.today()
        if t0.day == 1 and W.enabled() and not DRY and not MANUAL:
            import members
            from app import app as flask_app
            with flask_app.test_request_context('/', base_url='https://drapps.drscca.org'):
                n = members.send_books()
            say.append(f'worker points journal entry for {n} unreconciled entries emailed to the treasurer' if n else 'worker points: nothing to book')
    except Exception as e:
        say.append(f'worker points journal entry email failed: {e}')
    try:   # on the 1st: add this month's Auto add points (they count right away)
        t0 = date.today()
        if t0.day == 1 and W.enabled() and not DRY and not MANUAL:
            n = W.prepare_auto(t0.year, t0.month)
            say.append(f'auto add: {n} entries added')
    except Exception as e:
        say.append(f'auto add failed: {e}')
    print(f"QuickBooks sync {as_of}{' (dry run)' if DRY else ''}: " + '\n'.join(say))


ATTACHED = os.path.join(ROOT, 'data', 'qbo-attached.json')   # {request id: [Drive file ids already sent]}


def qbo_writes(data, reqs, say):
    """Attach paid requests' receipts in QuickBooks. Returns exception rows for what failed."""
    from storage import drive_files, drive_download
    import re
    try:
        with open(ATTACHED) as f:
            done = json.load(f)
    except (OSError, ValueError):
        done = {}
    exc = []
    with tempfile.TemporaryDirectory() as tmp:
        jobs, ids = [], {}
        for r in reqs:
            m = re.search(r'/folders/([\w-]+)', r.get('receipts_url') or '')
            if r['status'] != 'Paid' or not m:
                continue
            target = Q.payment_of(r, data['payments'])
            if not target:
                continue
            new = [f for f in drive_files(m.group(1)) if f['id'] not in done.get(r['id'], [])]
            if not new:
                continue
            files = []
            for n, f in enumerate(new):
                ct = Q.attach_type(f['name'])
                if not ct:
                    say.append(f"{r['id']}: QuickBooks cannot take {f['name']}, left in Drive only")
                    continue
                path = os.path.join(tmp, f"{r['id']}-{n}")
                if not DRY:
                    drive_download(f['id'], path)
                files.append({'path': path, 'name': f"{r['id']} {f['name']}"[:200], 'contentType': ct})
            if not files:           # nothing QuickBooks can take: remember them so they are not looked at again
                if not DRY:
                    done[r['id']] = done.get(r['id'], []) + [f['id'] for f in new]
                continue
            ids[r['id']] = [f['id'] for f in new]
            jobs.append({'op': 'attach', 'ref': r['id'], 'type': target[0], 'id': target[1], 'files': files})
        if not jobs:
            if not DRY:
                _save(done)
            return exc
        if DRY:
            for j in jobs:
                say.append(f"would attach {len(j['files'])} files of {j['ref']} to {j['type']} {j['id']}")
            return exc
        res = json.loads(subprocess.run(['node', os.path.join(HERE, 'qbo_write.mjs')], input=json.dumps(jobs), check=True,
                                        capture_output=True, text=True).stdout)
    for j, r in zip(jobs, res):
        if not r['ok']:
            say.append(f"QuickBooks {j['op']} {j['ref']} failed: {r['error']}")
            exc.append({'key': f"qbowrite:{j['op']}:{j['ref']}", 'kind': 'QuickBooks write failed', 'date': data['as_of'], 'name': j['ref'],
                        'amount': None, 'detail': f"Could not attach the receipts of {j['ref']}: {r['error']}"[:300],
                        'link': j['ref']})
        else:
            done[j['ref']] = done.get(j['ref'], []) + ids[j['ref']]
            if r['added']:
                say.append(f"{j['ref']}: {len(r['added'])} receipts attached to QuickBooks {j['type']} {j['id']}")
                log_audit_event('FUNDS_RECEIPTS_TO_QBO', WHO, {'id': j['ref'], 'files': len(r['added']), 'txn': f"{j['type']} {j['id']}"})
    _save(done)
    return exc


def _save(done):
    tmpf = ATTACHED + '.tmp'
    with open(tmpf, 'w') as f:
        json.dump(done, f)
    os.replace(tmpf, ATTACHED)


def run():
    """One sync at a time (the nightly timer and the Refresh button can meet), then note when it finished."""
    from datetime import datetime
    from filelock import FileLock
    with FileLock(os.path.join(ROOT, 'data', 'qbo-sync.lock')):
        main()
    if not DRY:
        tmp = os.path.join(ROOT, 'data', 'qbo-last-run.json.tmp')
        with open(tmp, 'w') as f:
            json.dump({'at': datetime.now().astimezone().isoformat(timespec='seconds'), 'manual': MANUAL}, f)
        os.replace(tmp, os.path.join(ROOT, 'data', 'qbo-last-run.json'))


if __name__ == '__main__':
    run()
