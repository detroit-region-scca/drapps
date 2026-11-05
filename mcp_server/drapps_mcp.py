"""MCP server for DRSCCA Apps (drapps.drscca.org): a thin wrapper around its JSON API (/api/v1, see api.py).

Config from the environment, or from the file named by DRAPPS_ENV (default ~/.secrets/drevent-api.env):
  DRAPPS_URL      https://drapps.drscca.org
  DRAPPS_API_KEY  a key from the app's API_KEYS (acts as that official; full read and write for now)
The older DREVENT_* names still work.

Receipts are local file paths on the machine running this server; they are uploaded with the request.
"""
import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer


def _config():
    """(api url, key), read fresh on every call from the environment or the DREVENT_ENV file, so a changed address
    or key takes effect without restarting every Claude session that runs this server."""
    env = {}
    f = Path(os.path.expanduser(os.environ.get('DRAPPS_ENV') or os.environ.get('DREVENT_ENV') or '~/.secrets/drevent-api.env'))
    if f.exists():
        for line in f.read_text().splitlines():
            k, _, v = line.partition('=')
            if k.strip() and not k.startswith('#'):
                env[k.strip()] = v.strip()
    pick = lambda k: os.environ.get(f'DRAPPS_{k}') or os.environ.get(f'DREVENT_{k}') or env.get(f'DRAPPS_{k}') or env.get(f'DREVENT_{k}')  # noqa: E731
    url = pick('URL') or 'https://drapps.drscca.org'
    return url.rstrip('/') + '/api/v1', pick('API_KEY') or ''


server = MCPServer('drapps', instructions=(
    'DRSCCA Apps (Detroit Region SCCA back office, drapps.drscca.org): events, event and program budgets, annual plans, '
    'funds requests, invoices, QuickBooks actuals, Operations (to-dos, recurring costs, vehicles, storage, documents), '
    'members and worker points, the Board (budgets and motions waiting for a vote, decisions) and volunteers. '
    'The board approves budgets, not plans. Program budgets have codes like RY-2027; event codes start with #. '
    'Funds request methods: check, card (region credit card, already paid), reimburse. Writes go through the app\'s own '
    'forms, so they send the same emails and audit entries as the website, signed as the key\'s official.'))


def _call(method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
    url, key = _config()
    if not key:
        return {'error': 'DRAPPS_API_KEY is not set (see ~/.secrets/drevent-api.env).'}
    r = httpx.request(method, url + path, json=body, params={k: v for k, v in (params or {}).items() if v not in (None, '')},
                      headers={'Authorization': f'Bearer {key}'}, timeout=120)
    try:
        data = r.json()
    except ValueError:
        data = {'error': f'HTTP {r.status_code}', 'body': r.text[:500]}
    if r.status_code >= 400 and isinstance(data, dict):
        data.setdefault('status', r.status_code)
    return data


def _q(code: str) -> str:
    return code.replace('#', '%23')


def _receipts(paths: list[str] | None) -> list[dict]:
    out = []
    for p in paths or []:
        f = Path(os.path.expanduser(p))
        out.append({'filename': f.name, 'content_base64': base64.b64encode(f.read_bytes()).decode(),
                    'content_type': mimetypes.guess_type(f.name)[0] or 'application/octet-stream'})
    return out


# ---------- reading ----------

@server.tool()
def whoami() -> Any:
    """Which official the API key acts as, and the API's endpoints."""
    return _call('GET', '/')


@server.tool()
def list_programs() -> Any:
    """Program codes and names (AX, RR, RY, RX, SS, SIM, DR, OPS)."""
    return _call('GET', '/programs')


@server.tool()
def list_officials() -> Any:
    """Region officials: roles, programs, names, emails (who gets approval emails)."""
    return _call('GET', '/officials')


@server.tool()
def list_events(year: str = '', program: str = '') -> Any:
    """Registered events, optionally for one year (YYYY) and program code."""
    return _call('GET', '/events', params={'year': year, 'program': program})


@server.tool()
def get_event(code: str) -> Any:
    """One event by code (#DR...): the event, its budget, QuickBooks actuals, plan line and funds requests."""
    return _call('GET', f'/events/{_q(code)}')


@server.tool()
def list_budgets(year: str = '') -> Any:
    """Event and program budgets, optionally for one year."""
    return _call('GET', '/budgets', params={'year': year})


@server.tool()
def get_budget(code: str) -> Any:
    """One budget by event code (#DR...) or program budget code (like RY-2027)."""
    return _call('GET', f'/budgets/{_q(code)}')


@server.tool()
def get_plans(year: str) -> Any:
    """Every program's annual plan for a year: lines (item, month, net, event_code, account), status info and net."""
    return _call('GET', f'/plans/{year}')


@server.tool()
def get_plan(year: str, program: str) -> Any:
    """One program's annual plan for a year."""
    return _call('GET', f'/plans/{year}/{program}')


@server.tool()
def get_actuals(year: str) -> Any:
    """QuickBooks income, expense and net by program for a year (from the nightly sync)."""
    return _call('GET', f'/actuals/{year}')


@server.tool()
def list_funds_requests(status: str = '', year: str = '', program: str = '') -> Any:
    """Funds requests. status: Submitted, Paid or Rejected. Each has approvals_needed, ready and waiting_on."""
    return _call('GET', '/funds', params={'status': status, 'year': year, 'program': program})


@server.tool()
def get_funds_request(req_id: str) -> Any:
    """One funds request (FR-YYYY-NNN) with its lines and what approvals it still needs."""
    return _call('GET', f'/funds/{req_id}')


@server.tool()
def list_invoices() -> Any:
    """All invoices."""
    return _call('GET', '/invoices')


@server.tool()
def get_invoice(inv_id: str) -> Any:
    """One invoice."""
    return _call('GET', f'/invoices/{inv_id}')


@server.tool()
def recent_audit(limit: int = 50) -> Any:
    """The newest entries of the app's audit log."""
    return _call('GET', '/audit', params={'limit': limit})


# ---------- writing ----------

@server.tool()
def register_event(program: str, date: str, event_name: str, custom_tag: str = '', plan_line: str = '') -> Any:
    """Register an event and get its code. date is YYYY-MM-DD. plan_line ties it to an annual plan line (its sort number).
    Emails the new-event notice like the website."""
    return _call('POST', '/events', {'program': program, 'date': date, 'event_name': event_name, 'custom_tag': custom_tag,
                                     'plan_line': plan_line})


@server.tool()
def save_budget(code: str, lines: list[dict], chair: str = '', location: str = '', notes: str = '') -> Any:
    """Create or replace a budget. code: event code (#DR...) or program budget code (RY-2027). lines:
    [{"section": "Income"|"Expense", "description", "budget", "last_year", "actual"}]. Saving an approved budget marks it changed."""
    return _call('PUT', f'/budgets/{_q(code)}', {'lines': lines, 'chair': chair, 'location': location, 'notes': notes})


@server.tool()
def delete_budget(code: str) -> Any:
    """Delete a budget (its files stay in Drive). Logged."""
    return _call('DELETE', f'/budgets/{_q(code)}')


@server.tool()
def approve_budget(code: str, date: str) -> Any:
    """Record the board meeting date (YYYY-MM-DD) that approved one budget; date "" clears the approval."""
    return _call('POST', f'/budgets/{_q(code)}/approve', {'date': date})


@server.tool()
def approve_budgets(year: str, codes: list[str], date: str) -> Any:
    """Record one board meeting's approval for several budgets of a year. Use "OPS" for the Operations budget."""
    return _call('POST', '/budgets/approve', {'year': year, 'codes': codes, 'date': date})


@server.tool()
def save_plan(year: str, program: str, lines: list[dict], notes: str = '', submit: bool = False) -> Any:
    """Replace a program's annual plan. lines: [{"item", "month" (1-12 or null), "net" (profit; costs negative),
    "note", "event_code", "account"}]. submit=True turns it in. The program budget line is added by the app, not here."""
    return _call('PUT', f'/plans/{year}/{program}', {'lines': lines, 'notes': notes, 'submit': submit})


@server.tool()
def approve_operations_budget(year: str, date: str) -> Any:
    """Record the board approval of the Operations budget (its plan) for a year; date "" clears it."""
    return _call('POST', f'/plans/{year}/OPS/approve', {'date': date})


@server.tool()
def create_funds_request(program: str, method: str, lines: list[dict], payee_name: str, event_code: str = '',
                         payee_email: str = '', payee_address: str = '', notes: str = '', advances: float = 0,
                         receipt_paths: list[str] | None = None, submitter_name: str = '', submitter_email: str = '') -> Any:
    """Submit a funds request. method: check (needs payee_address), card (already paid on the region card) or reimburse.
    lines: [{"date": "YYYY-MM-DD", "description", "category": Site Rental|Supplies|Professional Service|Storage|Travel Cost|Other,
    "amount"}]. receipt_paths: local files to attach. Submitter defaults to the key's official. Emails the treasurer
    and the approvers like the website."""
    return _call('POST', '/funds', {'program': program, 'event_code': event_code, 'method': method, 'lines': lines,
                                    'payee': {'name': payee_name, 'email': payee_email, 'address': payee_address},
                                    'submitter': {'name': submitter_name, 'email': submitter_email},
                                    'notes': notes, 'advances': advances, 'receipts': _receipts(receipt_paths)})


@server.tool()
def add_receipts(req_id: str, receipt_paths: list[str]) -> Any:
    """Attach more receipt files to a funds request."""
    return _call('POST', f'/funds/{req_id}/receipts', {'receipts': _receipts(receipt_paths)})


@server.tool()
def approve_funds_request(req_id: str, role: str = 'chair', meeting: str = '') -> Any:
    """Approve a funds request as the key's official. role: chair (program director), second (RE/ARE over $2,500) or
    board (meeting = board meeting date YYYY-MM-DD)."""
    return _call('POST', f'/funds/{req_id}/approve', {'role': role, 'meeting': meeting})


@server.tool()
def pay_funds_request(req_id: str, paid_ref: str = '', paid_date: str = '', paid_amount: float | None = None, note: str = '') -> Any:
    """Record a funds request as paid. paid_ref: check number or card; a card request defaults to "Region card" and its
    purchase date. Needs every approval in."""
    return _call('POST', f'/funds/{req_id}/pay', {'paid_ref': paid_ref, 'paid_date': paid_date, 'paid_amount': paid_amount, 'note': note})


@server.tool()
def reject_funds_request(req_id: str, note: str) -> Any:
    """Reject a funds request, saying why."""
    return _call('POST', f'/funds/{req_id}/reject', {'note': note})


@server.tool()
def reopen_funds_request(req_id: str) -> Any:
    """Reopen a paid or rejected funds request."""
    return _call('POST', f'/funds/{req_id}/reopen', {})


@server.tool()
def create_invoice(program: str, bill_name: str, lines: list[dict], event_code: str = '', bill_contact: str = '',
                   bill_email: str = '', bill_address: str = '', due: str = '', notes: str = '') -> Any:
    """Create an invoice (PDF saved to Drive and emailed to the treasurer). lines: [{"description", "quantity", "unit_price"}]."""
    return _call('POST', '/invoices', {'program': program, 'event_code': event_code, 'bill_name': bill_name, 'bill_contact': bill_contact,
                                       'bill_email': bill_email, 'bill_address': bill_address, 'due': due, 'notes': notes, 'lines': lines})


# ---------- Operations ----------

@server.tool()
def list_operations_tasks(category: str = '', due_before: str = '', only_dated: bool = False) -> Any:
    """Operations to-dos and recurring costs (insurance, registrations, filings, subscriptions), soonest first.
    due_before: YYYY-MM-DD. Each has id (TK-NNN), task, about, due, repeat_months, cost, owner, notes, auto (charged automatically)."""
    return _call('GET', '/operations/tasks', params={'category': category, 'due_before': due_before, 'open': '1' if only_dated else ''})


@server.tool()
def list_operations_items(category: str = '') -> Any:
    """Region property: vehicles, trailers and gear. category: Vehicle, Trailer, ... or empty for all."""
    return _call('GET', '/operations/items', params={'category': category})


@server.tool()
def list_storage_places() -> Any:
    """Storage places the region uses, with costs and access."""
    return _call('GET', '/operations/places')


@server.tool()
def list_operations_documents() -> Any:
    """Documents rows (titles, registrations, insurance cards, agreements) and their Drive folders."""
    return _call('GET', '/operations/documents')


@server.tool()
def add_operations_task(task: str, due: str = '', about: str = '', repeat_months: int | None = None, cost: float | None = None,
                        owner: str = '', notes: str = '', category: str = '', paid_how: str = '', account: str = '') -> Any:
    """Add an Operations to-do or recurring cost. due: YYYY-MM-DD. repeat_months: 12 for yearly, empty for one time.
    category: Administration, Insurance, Vehicles, Storage, Safety, Software and web, Travel, Other."""
    body = {'task': task, 'due': due, 'about': about, 'owner': owner, 'notes': notes, 'category': category, 'paid_how': paid_how,
            'account': account}
    if repeat_months is not None:
        body['repeat_months'] = repeat_months
    if cost is not None:
        body['cost'] = cost
    return _call('POST', '/operations/tasks', body)


@server.tool()
def update_operations_task(task_id: str, fields: dict) -> Any:
    """Change an Operations to-do (TK-NNN). fields: any of task, about, due, repeat_months, cost, owner, notes, category,
    paid_how, account, ends. Only the fields given change."""
    return _call('PUT', f'/operations/tasks/{task_id}', fields)


@server.tool()
def complete_operations_task(task_id: str) -> Any:
    """Mark an Operations to-do done: records who and when; a repeating one moves to its next due date."""
    return _call('POST', f'/operations/tasks/{task_id}/done', {})


# ---------- Members and worker points ----------

@server.tool()
def find_members(query: str = '') -> Any:
    """Members by part of a name or member number: number, name, worker points, life member."""
    return _call('GET', '/members', params={'q': query})


@server.tool()
def get_member(member: str) -> Any:
    """One member by number, with their worker points history."""
    return _call('GET', f'/members/{member}')


@server.tool()
def add_worker_points(entries: list[dict], event_code: str = '', what: str = '', date: str = '') -> Any:
    """Worker points, as on Members > Enter. entries: [{"member": number or full name, "points": 1}].
    event_code (#DR...) for an event; otherwise what (the volunteer job) and date (YYYY-MM-DD). Emails the points keeper."""
    return _call('POST', '/members/points', {'entries': entries, 'event_code': event_code, 'what': what, 'date': date})


# ---------- Board ----------

@server.tool()
def board_waiting() -> Any:
    """Everything waiting for the board: budgets not yet approved (year, code, name, status) and open motions."""
    return _call('GET', '/board')


@server.tool()
def board_decisions(year: str) -> Any:
    """Every recorded board decision in a year (budgets and motions), by date, with minutes when published."""
    return _call('GET', f'/board/decisions/{year}')


@server.tool()
def propose_motion(title: str, details: str = '', link: str = '') -> Any:
    """Add a motion to Waiting for the board (not sent yet). title: the motion in one line."""
    return _call('POST', '/board/motions', {'title': title, 'details': details, 'link': link})


@server.tool()
def request_board_vote(budgets: list[dict] | None = None, motions: list[str] | None = None, urgent: bool = False) -> Any:
    """Email the board (bod@) asking for a vote. budgets: [{"year", "code"}] from board_waiting; motions: ids.
    Urgent applies to the whole email, so send an urgent item alone."""
    return _call('POST', '/board/ask', {'budgets': budgets or [], 'motions': motions or [], 'urgent': urgent})


@server.tool()
def record_board_decision(result: str, date: str, budgets: list[dict] | None = None, motions: list[str] | None = None,
                          how: str = 'meeting', vote: str = '') -> Any:
    """Record the board's decision on budgets and motions. result: passed or failed. date: YYYY-MM-DD.
    how: meeting or email. vote: like 7-0. A failed budget stays waiting."""
    return _call('POST', '/board/record', {'budgets': budgets or [], 'motions': motions or [], 'result': result, 'date': date,
                                           'how': how, 'vote': vote})


@server.tool()
def withdraw_motion(motion_id: str) -> Any:
    """Withdraw an open motion (whoever proposed it, or an officer)."""
    return _call('POST', f'/board/motions/{motion_id}/withdraw', {})


# ---------- Volunteers ----------

@server.tool()
def list_volunteers() -> Any:
    """Volunteers who can sign in (not officials)."""
    return _call('GET', '/volunteers')


@server.tool()
def add_volunteer(name: str, email: str, note: str = '') -> Any:
    """Let a volunteer sign in with this email address. note: e.g. RX event chair."""
    return _call('POST', '/volunteers', {'name': name, 'email': email, 'note': note})


@server.tool()
def remove_volunteer(email: str) -> Any:
    """Remove a volunteer's sign-in."""
    return _call('DELETE', f'/volunteers/{email}')


# ---------- Money received ----------

@server.tool()
def list_money_received(status: str = '', year: str = '') -> Any:
    """Cash and checks reported received (CR-YYYY-NNN). status: Received (not deposited yet) or Deposited."""
    return _call('GET', '/received', params={'status': status, 'year': year})


@server.tool()
def get_money_received(report_id: str) -> Any:
    """One money received report with its lines, counters, who holds it, and deposit details."""
    return _call('GET', f'/received/{report_id}')


@server.tool()
def report_money_received(program: str, received: str, lines: list[dict], event_code: str = '', counted_by: str = '',
                          second_counter: str = '', held_by: str = '', notes: str = '', file_paths: list[str] | None = None) -> Any:
    """Report cash or checks taken in. received: YYYY-MM-DD. lines: [{"description", "category": Entry fees|Merchandise|
    Food and drink|Sponsorship|Donation|Other, "form": "Cash"|"Check", "check_number", "amount"}]. counted_by and held_by
    default to the key's official. file_paths: photos of the count sheet or checks. Emails the treasurer."""
    return _call('POST', '/received', {'program': program, 'event_code': event_code, 'received': received, 'lines': lines,
                                       'counted_by': counted_by, 'second_counter': second_counter, 'held_by': held_by,
                                       'notes': notes, 'files': _receipts(file_paths)})


@server.tool()
def mark_money_deposited(report_id: str, deposit_date: str = '', deposit_ref: str = '', deposit_amount: float | None = None,
                         reopen: bool = False) -> Any:
    """Mark a money received report deposited (the nightly QuickBooks sync usually does this), or reopen=True to undo."""
    return _call('POST', f'/received/{report_id}/deposit', {'deposit_date': deposit_date, 'deposit_ref': deposit_ref,
                                                            'deposit_amount': deposit_amount, 'reopen': reopen})


if __name__ == '__main__':
    server.run()
