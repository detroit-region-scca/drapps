"""JSON API (/api/v1) for scripts and the MCP server.

Auth: `Authorization: Bearer <key>`. Keys come from API_KEYS in the environment, as "email:key" pairs separated by
commas; the email must be in the officials list, and the API acts as that official. For now a key has full access,
read and write, money actions included (the app is still being built; see the README before production).

Reads are JSON views of the stores. Writes are handed to the app's own form handlers inside the same process, so
every rule, email and audit entry is exactly what the web pages do, and emailed links use the real address.
"""
import base64
import hmac
import io
import os
import re
from datetime import date

from flask import Blueprint, current_app, g, jsonify, request, session
from werkzeug.datastructures import MultiDict

import bud_store as B
import fr_logic as FL
import fr_store as F
import inv_store as I
import officials as O
import plan_store as P
from utils import load_audit_logs, load_events, load_programs

bp = Blueprint('api', __name__, url_prefix='/api/v1')


def _keys():
    """{key: email} from API_KEYS="email:key,email:key"."""
    out = {}
    for pair in (os.environ.get('API_KEYS') or '').split(','):
        email, _, key = pair.strip().partition(':')
        if email and len(key) >= 32:
            out[key] = email.strip().lower()
    return out


def api_user():
    """{'email', 'name'} for a valid bearer key, else None."""
    h = request.headers.get('Authorization', '')
    if not h.startswith('Bearer '):
        return None
    given = h[7:].strip()
    for key, email in _keys().items():
        if hmac.compare_digest(given, key):
            name = next((o['name'] for o in O.all() if (o.get('email') or '').lower() == email), '')
            return {'email': email, 'name': name or email} if name else None
    return None


def signin_from_key():
    """before_request for the whole app: a valid key signs this request in as the user and as an officer."""
    u = api_user()
    if u:
        g.api_user = u
        session['authenticated'] = True
        session['email'] = u['email']        # so role checks (Board, Members) see the key's official, as on the website
        session['who'] = u['name']
        session['officer'] = {'email': u['email'], 'name': u['name']}
        import equip
        if u['email'] in equip.editors():    # Operations edits, as for the same person signed in
            session['equip_editor'] = {'email': u['email'], 'name': u['name']}


@bp.before_request
def _require_key():
    if not getattr(g, 'api_user', None):
        return jsonify(error='Missing or invalid API key (Authorization: Bearer <key>).'), 401


def _code(e):
    return e.get('custom_tag') or e.get('unique_code')


def _err(msg, status=400, **kw):
    return jsonify(error=msg, **kw), status


# ---------- reads ----------

@bp.get('/')
def index():
    return jsonify(user=g.api_user, endpoints=sorted(str(r) for r in current_app.url_map.iter_rules() if str(r).startswith('/api/v1')))


@bp.get('/programs')
def programs():
    return jsonify(dict(load_programs(), OPS='Operations'))


@bp.get('/officials')
def officials():
    return jsonify(O.all())


@bp.get('/events')
def events():
    year, program = request.args.get('year', ''), request.args.get('program', '')
    rows = [dict(e, code=_code(e)) for e in load_events()
            if e.get('date', '').startswith(year) and (not program or e.get('program_code') == program)]
    return jsonify(sorted(rows, key=lambda e: e.get('date', '')))


@bp.get('/events/<path:code>')
def event(code):
    e = next((e for e in load_events() if _code(e) == code), None)
    b = B.get(code) if B.enabled() else None
    if not e and not b:
        return _err('Event not found.', 404)
    funds = [r for r in F.requests() if r['event_code'] == code] if F.enabled() else []
    return jsonify(event=dict(e, code=code) if e else None, budget=b, qbo=B.qbo_actuals().get(code) if B.enabled() else None,
                   plan_line=P.line_for(code) if P.enabled() else None, funds=funds)


@bp.get('/budgets')
def budgets():
    year = request.args.get('year', '')
    return jsonify({c: b for c, b in B.budgets().items() if str(b.get('date', '')).startswith(year)})


@bp.get('/budgets/<path:code>')
def budget(code):
    b = B.get(code)
    return jsonify(b) if b else _err('No budget with that code.', 404)


@bp.get('/plans/<year>')
def plans(year):
    return jsonify(P.plan(year))


@bp.get('/plans/<year>/<program>')
def plan(year, program):
    p = P.plan(year).get(program)
    return jsonify(p) if p else _err('No plan for that program and year.', 404)


@bp.get('/actuals/<year>')
def actuals(year):
    acts, as_of = P.actuals(year)
    return jsonify(as_of=as_of, programs=acts)


def _with_review(r, a):
    items = a.items(r) if r['status'] == 'Submitted' else []
    return dict(r, approvals_needed=items, ready=FL.ready(items) if r['status'] == 'Submitted' else None,
                waiting_on=FL.waiting_on(items))


@bp.get('/funds')
def funds():
    import fr
    a = fr.Approvals()
    st, year, program = request.args.get('status', ''), request.args.get('year', ''), request.args.get('program', '')
    rows = [r for r in a.reqs if (not st or r['status'] == st) and r['submitted'].startswith(year) and (not program or r['program'] == program)]
    return jsonify([_with_review(r, a) for r in rows])


@bp.get('/funds/<req_id>')
def fund(req_id):
    import fr
    r = F.get(req_id)
    return jsonify(_with_review(r, fr.Approvals())) if r else _err('Funds request not found.', 404)


@bp.get('/invoices')
def invoices():
    return jsonify(I.invoices())


@bp.get('/invoices/<inv_id>')
def invoice(inv_id):
    inv = I.get(inv_id)
    return jsonify(inv) if inv else _err('Invoice not found.', 404)


@bp.get('/audit')
def audit():
    n = min(int(request.args.get('limit', 100) or 100), 2000)
    return jsonify(load_audit_logs()[-n:])


# ---------- writes: handed to the app's own form handlers ----------

def _forward(path, form, files=()):
    """POST a form to one of the app's own pages as the key's user, in this process. Returns (status, location,
    flashes). The base URL is the address this API was called on, so emailed links are right."""
    c = current_app.test_client()
    data = MultiDict()
    for k, v in form.items():
        for x in (v if isinstance(v, list) else [v]):
            data.add(k, '' if x is None else str(x))
    for field, name, content, mime in files:
        data.add(field, (io.BytesIO(content), name, mime))
    r = c.post(path, data=data, base_url=request.host_url, content_type='multipart/form-data',
               headers={'Authorization': request.headers['Authorization']})
    with c.session_transaction(base_url=request.host_url) as s:
        flashes = [{'category': cat, 'message': msg} for cat, msg in s.get('_flashes', [])]
    return r.status_code, r.headers.get('Location', ''), flashes


def _result(status, location, flashes, ok_status=(302, 303)):
    errors = [f['message'] for f in flashes if f['category'] in ('danger', 'warning')]
    ok = status in ok_status and not any(f['category'] == 'danger' for f in flashes)
    return ok, errors


def _files(items, field):
    out = []
    for it in items or []:
        try:
            content = base64.b64decode(it['content_base64'], validate=True)
        except Exception:
            raise ValueError(f"Receipt {it.get('filename', '?')}: content_base64 is not valid base64.")
        out.append((field, it.get('filename') or 'receipt', content, it.get('content_type') or 'application/octet-stream'))
    return out


def _body():
    return request.get_json(silent=True) or {}


@bp.post('/events')
def create_event():
    d = _body()
    st, loc, fl = _forward('/events/new', {'program': d.get('program', ''), 'date': d.get('date', ''), 'event_name': d.get('event_name', ''),
                                           'your_name': d.get('your_name') or g.api_user['name'], 'custom_tag': d.get('custom_tag', ''),
                                           'plan_line': d.get('plan_line', '')})
    m = re.search(r'/events/event/([^/?]+)$', loc)
    if not m:
        return _err('Not registered.', flashes=fl)
    code = m.group(1).replace('%23', '#')
    return jsonify(code=code, event=next((dict(e, code=code) for e in load_events() if _code(e) == code), None), flashes=fl), 201


@bp.put('/budgets/<path:code>')
def save_budget(code):
    d = _body()
    form = {'chair': d.get('chair', ''), 'location': d.get('location', ''), 'notes': d.get('notes', ''), 'entered_by': g.api_user['name']}
    for sec, key in (('Income', 'inc'), ('Expense', 'exp')):
        ls = [ln for ln in d.get('lines', []) if ln.get('section') == sec]
        form.update({f'{key}_desc': [ln.get('description', '') for ln in ls], f'{key}_ly': [ln.get('last_year') for ln in ls],
                     f'{key}_bud': [ln.get('budget') for ln in ls], f'{key}_act': [ln.get('actual') for ln in ls]})
    st, loc, fl = _forward(f"/events/event/{code.replace('#', '%23')}/edit", form)
    ok, errors = _result(st, loc, fl)
    return (jsonify(budget=B.get(code), flashes=fl), 200) if ok else _err('Budget not saved.', errors=errors, flashes=fl)


@bp.delete('/budgets/<path:code>')
def delete_budget(code):
    st, loc, fl = _forward(f"/events/event/{code.replace('#', '%23')}/delete", {})
    return (jsonify(deleted=code, flashes=fl), 200) if not B.get(code) else _err('Not deleted.', flashes=fl)


@bp.post('/budgets/approve')
def approve_budgets():
    """Record one board meeting's approval for several budgets: {"year", "codes": [...], "date"}. "OPS" is the
    Operations budget (its plan)."""
    d = _body()
    st, loc, fl = _forward(f"/events/approve?year={d.get('year', date.today().year)}", {'code': d.get('codes', []), 'approved': d.get('date', '')})
    ok, errors = _result(st, loc, fl)
    return (jsonify(flashes=fl), 200) if ok else _err('Not recorded.', errors=errors, flashes=fl)


@bp.post('/budgets/<path:code>/approve')
def approve_budget(code):
    """{"date": "YYYY-MM-DD"} records the board approval; {"date": ""} clears it."""
    st, loc, fl = _forward(f"/events/event/{code.replace('#', '%23')}/approve", {'approved': _body().get('date', '')})
    ok, errors = _result(st, loc, fl)
    return (jsonify(budget=B.get(code), flashes=fl), 200) if ok else _err('Not recorded.', errors=errors, flashes=fl)


@bp.put('/plans/<year>/<program>')
def save_plan(year, program):
    """{"lines": [{"item", "month", "net", "note", "event_code", "account"}], "notes", "submit": bool}. Replaces the plan."""
    d = _body()
    ls = d.get('lines', [])
    form = {'item': [ln.get('item', '') for ln in ls], 'month': [ln.get('month') or '' for ln in ls], 'net': [ln.get('net') for ln in ls],
            'note': [ln.get('note', '') for ln in ls], 'code': [ln.get('event_code', '') for ln in ls], 'account': [ln.get('account', '') for ln in ls],
            'notes': d.get('notes', ''), 'entered_by': g.api_user['name'], 'action': 'submit' if d.get('submit') else 'save'}
    st, loc, fl = _forward(f'/plan/{year}/{program}/edit', form)
    ok, errors = _result(st, loc, fl)
    return (jsonify(plan=P.plan(year).get(program), flashes=fl), 200) if ok else _err('Plan not saved.', errors=errors, flashes=fl)


@bp.post('/plans/<year>/<program>/approve')
def approve_plan(year, program):
    st, loc, fl = _forward(f'/plan/{year}/{program}/approve', {'approved': _body().get('date', '')})
    ok, errors = _result(st, loc, fl)
    return (jsonify(flashes=fl), 200) if ok else _err('Not recorded.', errors=errors, flashes=fl)


@bp.post('/funds')
def create_funds_request():
    """{"program", "event_code", "method": "check"|"card"|"reimburse", "lines": [{"date", "description", "category", "amount"}],
    "payee": {"name", "email", "phone", "address"}, "submitter": {...}, "advances", "notes",
    "receipts": [{"filename", "content_base64", "content_type"}]}"""
    d = _body()
    ls, sub, pay = d.get('lines', []), d.get('submitter') or {}, d.get('payee') or {}
    form = {'program': d.get('program', ''), 'event_code': d.get('event_code', ''), 'method': d.get('method', ''),
            'advances': d.get('advances', 0), 'notes': d.get('notes', ''),
            'line_date': [ln.get('date', '') for ln in ls], 'line_desc': [ln.get('description', '') for ln in ls],
            'line_cat': [ln.get('category', 'Other') for ln in ls], 'line_amount': [ln.get('amount') for ln in ls],
            **{f'submitter_{k}': sub.get(k) or (g.api_user['name'] if k == 'name' else g.api_user['email'] if k == 'email' else '')
               for k in ('name', 'email', 'phone', 'address')},
            **{f'payee_{k}': pay.get(k, '') for k in ('name', 'email', 'phone', 'address')}}
    try:
        files = _files(d.get('receipts'), 'receipts')
    except ValueError as e:
        return _err(str(e))
    st, loc, fl = _forward('/funds/new', form, files)
    m = re.search(r'/funds/(FR-[\w-]+)$', loc)
    if not m:
        return _err('Not submitted.', errors=[f['message'] for f in fl], flashes=fl)
    return jsonify(id=m.group(1), request=F.get(m.group(1)), flashes=fl), 201


@bp.post('/funds/<req_id>/receipts')
def add_receipts(req_id):
    try:
        files = _files(_body().get('receipts'), 'receipts')
    except ValueError as e:
        return _err(str(e))
    st, loc, fl = _forward(f'/funds/{req_id}/receipts', {}, files)
    return jsonify(request=F.get(req_id), flashes=fl)


@bp.post('/funds/<req_id>/approve')
def approve_funds(req_id):
    """{"role": "chair"|"second"|"board", "meeting": "YYYY-MM-DD" (board only)}: approve as the key's user."""
    import fr
    d = _body()
    role = d.get('role', 'chair')
    if role not in FL.ROLES:
        return _err(f"role must be one of {', '.join(FL.ROLES)}.")
    token = fr._serializer().dumps({'id': req_id, 'role': role, 'name': g.api_user['name'], 'email': g.api_user['email']})
    st, loc, fl = _forward(f'/funds/approve/{token}', {'action': 'approve', 'meeting': d.get('meeting', '')})
    r = F.get(req_id)
    return jsonify(request=_with_review(r, fr.Approvals()) if r else None, flashes=fl)


def _close(req_id, form):
    import fr
    st, loc, fl = _forward(f'/funds/{req_id}/close', form)
    ok, errors = _result(st, loc, fl)
    r = F.get(req_id)
    body = dict(request=_with_review(r, fr.Approvals()) if r else None, flashes=fl)
    return (jsonify(body), 200) if ok else (jsonify(error='Not done.', errors=errors, **body), 400)


@bp.post('/funds/<req_id>/pay')
def pay_funds(req_id):
    """Record payment: {"paid_ref", "paid_date", "paid_amount", "note"}; a card request defaults to "Region card"."""
    d, r = _body(), F.get(req_id)
    if not r:
        return _err('Funds request not found.', 404)
    card = r['method'] == 'Region credit card'
    dates = [ln['date'] for ln in r.get('lines', []) if ln.get('date')]
    return _close(req_id, {'action': 'paid', 'paid_ref': d.get('paid_ref') or ('Region card' if card else ''),
                           'paid_date': d.get('paid_date') or (max(dates) if card and dates else date.today().isoformat()),
                           'paid_amount': d.get('paid_amount') or r['net'], 'note': d.get('note', '')})


@bp.post('/funds/<req_id>/reject')
def reject_funds(req_id):
    return _close(req_id, {'action': 'reject', 'note': _body().get('note', '')})


@bp.post('/funds/<req_id>/reopen')
def reopen_funds(req_id):
    return _close(req_id, {'action': 'reopen'})


@bp.post('/invoices')
def create_invoice():
    """{"program", "event_code", "bill_name", "bill_contact", "bill_email", "bill_address", "due", "notes",
    "lines": [{"description", "quantity", "unit_price"}]}"""
    d = _body()
    ls = d.get('lines', [])
    form = {k: d.get(k, '') for k in ('program', 'event_code', 'bill_name', 'bill_contact', 'bill_email', 'bill_address', 'due', 'notes')}
    form.update(desc=[ln.get('description', '') for ln in ls], qty=[ln.get('quantity', 1) for ln in ls], price=[ln.get('unit_price') for ln in ls])
    st, loc, fl = _forward('/invoices/new', form)
    m = re.search(r'/invoices/([^/?]+)$', loc)
    if not m or m.group(1) == 'new':
        return _err('Invoice not created.', errors=[f['message'] for f in fl], flashes=fl)
    return jsonify(id=m.group(1), invoice=I.get(m.group(1)), flashes=fl), 201


# ---------- Operations ----------

def _ops():
    import equip_store as E
    return E if E.enabled() else None


@bp.get('/operations/tasks')
def ops_tasks():
    """To-dos and recurring costs. ?category=, ?due_before=YYYY-MM-DD, ?open=1 (only ones with a due date)."""
    E = _ops()
    if not E:
        return _err('Operations is not set up.', 404)
    cat, before, only_open = request.args.get('category', ''), request.args.get('due_before', ''), request.args.get('open') == '1'
    rows = [t for t in E.tasks() if (not cat or t.get('category') == cat) and (not before or (t['due'] and t['due'] <= before))
            and (not only_open or t['due'])]
    return jsonify(sorted(rows, key=lambda t: (not t['due'], t['due'] or '', t['id'])))


@bp.get('/operations/items')
def ops_items():
    """Vehicles, trailers and gear. ?category=Vehicle|Trailer|..."""
    E = _ops()
    if not E:
        return _err('Operations is not set up.', 404)
    cat = request.args.get('category', '')
    return jsonify([i for i in E.items() if not cat or i.get('category') == cat])


@bp.get('/operations/places')
def ops_places():
    E = _ops()
    return jsonify(E.places()) if E else _err('Operations is not set up.', 404)


@bp.get('/operations/documents')
def ops_documents():
    E = _ops()
    return jsonify(E.documents()) if E else _err('Operations is not set up.', 404)


TASK_FIELDS = ('task', 'about', 'due', 'repeat_months', 'cost', 'owner', 'notes', 'category', 'paid_how', 'account', 'ends')


@bp.post('/operations/tasks')
def ops_add_task():
    """{"task", "about", "due", "repeat_months", "cost", "owner", "notes", "category", "paid_how", "account", "ends"}"""
    E = _ops()
    if not E:
        return _err('Operations is not set up.', 404)
    d = _body()
    before = {t['id'] for t in E.tasks()}
    st, loc, fl = _forward('/operations/tasks/new', {k: d[k] for k in TASK_FIELDS if k in d})
    ok, errors = _result(st, loc, fl)
    new = [t for t in E.tasks() if t['id'] not in before]
    return (jsonify(task=new[0] if new else None, flashes=fl), 201) if ok and new else _err('Not saved.', errors=errors, flashes=fl)


@bp.put('/operations/tasks/<task_id>')
def ops_update_task(task_id):
    """Change only the fields given (same names as adding)."""
    E = _ops()
    if not E or not any(t['id'] == task_id for t in E.tasks()):
        return _err('To-do not found.', 404)
    d = _body()
    st, loc, fl = _forward(f'/operations/tasks/{task_id}', {k: d[k] for k in TASK_FIELDS if k in d})
    ok, errors = _result(st, loc, fl)
    t = next((t for t in E.tasks() if t['id'] == task_id), None)
    return (jsonify(task=t, flashes=fl), 200) if ok else _err('Not saved.', errors=errors, flashes=fl)


@bp.post('/operations/tasks/<task_id>/done')
def ops_task_done(task_id):
    """Mark done: notes who and when; a repeating to-do moves to its next date."""
    E = _ops()
    if not E or not any(t['id'] == task_id for t in E.tasks()):
        return _err('To-do not found.', 404)
    st, loc, fl = _forward(f'/operations/tasks/{task_id}', {'action': 'done'})
    ok, errors = _result(st, loc, fl)
    t = next((t for t in E.tasks() if t['id'] == task_id), None)
    return (jsonify(task=t, flashes=fl), 200) if ok else _err('Not done.', errors=errors, flashes=fl)


# ---------- Members and worker points ----------

def _mem():
    import members_store as S
    return S if S.enabled() else None


@bp.get('/members')
def members_list():
    """?q= part of a name or member number. Number, name and points only."""
    S = _mem()
    if not S:
        return _err('Members is not set up.', 404)
    q = request.args.get('q', '').strip().lower()
    rows = [{'member': m['member'], 'first': m['first'], 'last': m['last'], 'points': m['points'], 'life': m['life']}
            for m in S.members() if not q or q in m['member'].lower() or q in f"{m['first']} {m['last']}".lower()]
    return jsonify(sorted(rows, key=lambda m: (m['last'].lower(), m['first'].lower())))


@bp.get('/members/<mid>')
def member_detail(mid):
    S = _mem()
    m = next((m for m in S.members() if m['member'] == mid), None) if S else None
    if not m:
        return _err('Member not found.', 404)
    hist = sorted([e for e in S.entries() if e['member'] == mid], key=lambda e: str(e.get('date') or ''), reverse=True)
    return jsonify(member=m, history=hist)


@bp.post('/members/points')
def members_add_points():
    """Worker points for an event or a volunteer job, as on Members > Enter.
    {"event_code": "#DR..." or "", "what": "job (when no event)", "date": "YYYY-MM-DD",
     "entries": [{"member": "number or full name", "points": 1}]}"""
    d = _body()
    es = d.get('entries') or []
    st, loc, fl = _forward('/members/enter', {'event': d.get('event_code') or 'none', 'what': d.get('what', ''), 'first': d.get('date', ''),
                                              'member': [str(e.get('member', '')) for e in es], 'points': [e.get('points', 1) for e in es]})
    ok, errors = _result(st, loc, fl)
    return (jsonify(flashes=fl), 201) if ok else _err('Not saved.', errors=errors, flashes=fl)


# ---------- Board ----------

@bp.get('/board')
def board_waiting():
    """Everything waiting for the board: budgets not yet approved and open motions."""
    import bud
    import motions_store as M
    rows = [dict(year=y, code=r['b'].get('event_code'), name=r['b'].get('event_name'), program=r['b'].get('program'),
                 date=str(r['b'].get('date', '')), status=r['status'], surplus=r['surplus'], plan=r['plan'])
            for y in bud.pending_years() for r in bud._pending_rows(y)] if B.enabled() else []
    return jsonify(budgets=rows, motions=[m for m in M.motions() if m['status'] == 'open'] if M.enabled() else [])


@bp.get('/board/decisions/<year>')
def board_decisions(year):
    import board
    return jsonify(board.decisions(year))


@bp.post('/board/motions')
def board_propose():
    """{"title": "the motion in one line", "details", "link"}"""
    import motions_store as M
    d = _body()
    before = {m['id'] for m in M.motions()}
    st, loc, fl = _forward('/board/motions', {'title': d.get('title', ''), 'details': d.get('details', ''), 'link': d.get('link', '')})
    new = [m for m in M.motions() if m['id'] not in before]
    return (jsonify(motion=new[0], flashes=fl), 201) if new else _err('Not added.', errors=[f['message'] for f in fl], flashes=fl)


def _board_items(d):
    return ([f"budget:{b['year']}:{b['code']}" for b in d.get('budgets') or []] + [f'motion:{m}' for m in d.get('motions') or []])


@bp.post('/board/ask')
def board_ask():
    """Email the board for a vote. {"budgets": [{"year", "code"}], "motions": ["MO-..."], "urgent": false}"""
    d = _body()
    st, loc, fl = _forward('/board/', {'item': _board_items(d), 'action': 'ask', 'urgent': 'yes' if d.get('urgent') else ''})
    ok, errors = _result(st, loc, fl)
    return (jsonify(flashes=fl), 200) if ok and not errors else _err('Not sent.', errors=errors, flashes=fl)


@bp.post('/board/record')
def board_record():
    """Record the board's decision. {"budgets": [...], "motions": [...], "result": "passed"|"failed", "date": "YYYY-MM-DD",
    "how": "meeting"|"email", "vote": "e.g. 7-0"}"""
    d = _body()
    st, loc, fl = _forward('/board/', {'item': _board_items(d), 'action': 'record', 'result': d.get('result', ''),
                                       'decided': d.get('date', ''), 'how': d.get('how', ''), 'vote': d.get('vote', '')})
    ok, errors = _result(st, loc, fl)
    return (jsonify(flashes=fl), 200) if ok and not errors else _err('Not recorded.', errors=errors, flashes=fl)


@bp.post('/board/motions/<mid>/withdraw')
def board_withdraw(mid):
    st, loc, fl = _forward(f'/board/motion/{mid}/withdraw', {})
    ok, errors = _result(st, loc, fl)
    return (jsonify(flashes=fl), 200) if ok and not errors else _err('Not withdrawn.', errors=errors, flashes=fl)


# ---------- Volunteers ----------

@bp.get('/volunteers')
def volunteers_list():
    import auth
    return jsonify(sorted(auth.volunteers().values(), key=lambda v: v.get('name', '').lower()))


@bp.post('/volunteers')
def volunteers_add():
    """{"name", "email", "note"}"""
    d = _body()
    st, loc, fl = _forward('/access', {'name': d.get('name', ''), 'email': d.get('email', ''), 'note': d.get('note', '')})
    ok, errors = _result(st, loc, fl)
    return (jsonify(flashes=fl), 201) if ok and not errors else _err('Not added.', errors=errors, flashes=fl)


@bp.delete('/volunteers/<email>')
def volunteers_remove(email):
    st, loc, fl = _forward('/access', {'action': 'remove', 'email': email})
    ok, errors = _result(st, loc, fl)
    return (jsonify(flashes=fl), 200) if ok and not errors else _err('Not removed.', errors=errors, flashes=fl)


# ---------- Money received (cash and checks) ----------

@bp.get('/received')
def received_list():
    """?status=Received|Deposited, ?year=YYYY"""
    import received_store as R
    st, year = request.args.get('status', ''), request.args.get('year', '')
    return jsonify([r for r in R.reports() if (not st or r['status'] == st) and r['received'].startswith(year)])


@bp.get('/received/<rid>')
def received_detail(rid):
    import received_store as R
    r = R.get(rid)
    return jsonify(r) if r else _err('Not found.', 404)


@bp.post('/received')
def received_create():
    """{"program", "event_code", "received": "YYYY-MM-DD", "counted_by", "second_counter", "held_by", "notes",
    "lines": [{"description", "category", "form": "Cash"|"Check", "check_number", "amount"}],
    "files": [{"filename", "content_base64", "content_type"}]}"""
    import received_store as R
    d = _body()
    ls = d.get('lines', [])
    form = {'program': d.get('program', ''), 'event_code': d.get('event_code', ''), 'received': d.get('received', ''),
            'counted_by': d.get('counted_by') or g.api_user['name'], 'second_counter': d.get('second_counter', ''),
            'held_by': d.get('held_by') or g.api_user['name'], 'notes': d.get('notes', ''),
            'line_desc': [ln.get('description', '') for ln in ls], 'line_cat': [ln.get('category', 'Other') for ln in ls],
            'line_form': [ln.get('form', 'Cash') for ln in ls], 'line_check': [ln.get('check_number', '') for ln in ls],
            'line_amount': [ln.get('amount') for ln in ls]}
    try:
        files = _files(d.get('files'), 'files')
    except ValueError as e:
        return _err(str(e))
    st, loc, fl = _forward('/funds/in/new', form, files)
    m = re.search(r'/funds/in/(CR-[\w-]+)$', loc)
    if not m:
        return _err('Not reported.', errors=[f['message'] for f in fl], flashes=fl)
    return jsonify(id=m.group(1), report=R.get(m.group(1)), flashes=fl), 201


@bp.post('/received/<rid>/deposit')
def received_deposit(rid):
    """{"deposit_date", "deposit_ref", "deposit_amount"}, or {"reopen": true} to mark it not deposited."""
    import received_store as R
    d = _body()
    form = {'action': 'reopen'} if d.get('reopen') else {k: d.get(k, '') for k in ('deposit_date', 'deposit_ref', 'deposit_amount')}
    st, loc, fl = _forward(f'/funds/in/{rid}/deposit', form)
    ok, errors = _result(st, loc, fl)
    return (jsonify(report=R.get(rid), flashes=fl), 200) if ok and not errors else _err('Not recorded.', errors=errors, flashes=fl)


@bp.delete('/received/<rid>')
def received_delete(rid):
    """Delete a report not yet deposited (a mistake or a duplicate). Kept in the audit log."""
    import received_store as R
    st, loc, fl = _forward(f'/funds/in/{rid}/delete', {})
    gone = R.get(rid) is None
    return (jsonify(deleted=rid, flashes=fl), 200) if gone else _err('Not deleted.', errors=[f['message'] for f in fl], flashes=fl)


@bp.delete('/funds/<req_id>')
def funds_delete(req_id):
    """Delete a funds request not yet paid or rejected (a mistake or a duplicate). Kept in the audit log."""
    st, loc, fl = _forward(f'/funds/{req_id}/delete', {})
    return (jsonify(deleted=req_id, flashes=fl), 200) if F.get(req_id) is None else _err('Not deleted.', errors=[f['message'] for f in fl], flashes=fl)
