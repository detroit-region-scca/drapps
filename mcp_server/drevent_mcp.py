"""MCP server for the DRSCCA drevent app: a thin wrapper around its JSON API (/api/v1, see api.py).

Config from the environment, or from the file named by DREVENT_ENV (default ~/.secrets/drevent-api.env):
  DREVENT_URL      https://drapps.drscca.org
  DREVENT_API_KEY  a key from the app's API_KEYS (acts as that official; full read and write for now)

Receipts are local file paths on the machine running this server; they are uploaded with the request.
"""
import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer


def _load_env():
    f = Path(os.path.expanduser(os.environ.get('DREVENT_ENV', '~/.secrets/drevent-api.env')))
    if f.exists():
        for line in f.read_text().splitlines():
            k, _, v = line.partition('=')
            if k.strip() and not k.startswith('#'):
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
URL = os.environ.get('DREVENT_URL', 'https://drapps.drscca.org').rstrip('/') + '/api/v1'
KEY = os.environ.get('DREVENT_API_KEY', '')

server = MCPServer('drevent', instructions=(
    'DRSCCA (Detroit Region SCCA) event and money app: events, event and program budgets, annual plans, funds requests, '
    'invoices, QuickBooks actuals. The board approves budgets, not plans. Program budgets have codes like RY-2027; '
    'event codes start with #. Funds request methods: check, card (region credit card, already paid), reimburse. '
    'Writes go through the app\'s own forms, so they send the same emails as the website.'))


def _call(method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
    if not KEY:
        return {'error': 'DREVENT_API_KEY is not set (see ~/.secrets/drevent-api.env).'}
    r = httpx.request(method, URL + path, json=body, params={k: v for k, v in (params or {}).items() if v not in (None, '')},
                      headers={'Authorization': f'Bearer {KEY}'}, timeout=120)
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


if __name__ == '__main__':
    server.run()
