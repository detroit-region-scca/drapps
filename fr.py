"""Funds Requests: replaces the emailed Excel Funds Request Form. Records live in the Funds Requests sheet (fr_store).

A request is paid only once it has the approvals the region policies ask for (fr_logic). The chair or program
director and, over $2,500, the RE or ARE get an email with a link that approves without the app password.
"""
import os
from datetime import date

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

import bud_store as B
import fr_logic as FL
import fr_store as F
import plan_store as P
from utils import load_events, load_programs, log_audit_event, login_required, send_mail

bp = Blueprint('fr', __name__, url_prefix='/funds')

ADDR = ('address', 'city')


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Funds Requests", who=session.get('who', ''),
                categories=F.CATEGORIES, methods=F.METHODS, **kw)


def _guard():
    if not F.enabled():
        flash('Funds requests need the Google Sheets storage (STORAGE_BACKEND=sheets and FUNDS_SHEET_ID).', 'warning')
        return redirect(url_for('home'))
    return None


def _money(v):
    try:
        return round(float(str(v).replace(',', '').replace('$', '').strip() or 0), 2)
    except ValueError:
        return None


def _events():
    return sorted(load_events(), key=lambda e: e.get('date', ''), reverse=True)


LINK_DAYS = 90


def _signers():
    """[(name, email)] from SECOND_SIGNERS, "Name <email>, Name <email>" (the RE and ARE)."""
    out = []
    for part in os.environ.get('SECOND_SIGNERS', '').split(','):
        name, _, mail = part.partition('<')
        if mail.strip(' >'):
            out.append((name.strip(), mail.strip(' >')))
    return out


class Approvals:
    """Everything review() needs, loaded once per page."""

    def __init__(self):
        self.reqs = F.requests()
        self.budgets = B.budgets() if B.enabled() else {}
        self.qbo = B.qbo_actuals() if B.enabled() else {}
        self.plans = {}

    def _plan(self, year):
        if year not in self.plans:
            self.plans[year] = P.plan(year) if P.enabled() else {}
        return self.plans[year]

    def director(self, r):
        info = self._plan(r['submitted'][:4]).get(r['program'], {}).get('info', {})
        return info.get('director', ''), info.get('email', '')

    def items(self, r):
        code, b = r['event_code'], self.budgets.get(r['event_code'])
        status, expense = None, None
        if b:
            status = 'changed' if b.get('approved') and b.get('updated', '') > b['approved'] else 'approved' if b.get('approved') else 'budget'
            expense = (b['totals'].get('budget') or {}).get('expense')
        spent = ((self.qbo.get(code) or {}).get('totals', {}).get('expense') or 0.0) if code else 0.0
        spent += sum(x['net'] for x in self.reqs if code and x['event_code'] == code and x['status'] == 'Submitted' and x['id'] != r['id'])
        plan = self._plan(r['submitted'][:4]).get(r['program'], {})
        return FL.review(r, status, expense, round(spent, 2), bool(plan.get('lines')), (b or {}).get('chair', ''), self.director(r)[0])


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='funds-approve')


def _link(rid, role, name, mail):
    token = _serializer().dumps({'id': rid, 'role': role, 'name': name, 'email': mail})
    return f"{request.url_root.rstrip('/')}{url_for('fr.approve_link', token=token)}"


def ask_approvals(rid, roles=None):
    """Email whoever still has to approve: the program director for the chair approval, the RE and ARE over the limit."""
    r = F.get(rid)
    if not r or r['status'] != 'Submitted':
        return []
    a = Approvals()
    missing = {i['key'] for i in a.items(r) if not i['done']} & set(roles or ('chair', 'second'))
    to = []
    if 'chair' in missing:
        name, mail = a.director(r)
        if mail:
            to.append(('chair', name, mail))
    if 'second' in missing:
        to += [('second', n, m) for n, m in _signers()]
    for role, name, mail in to:
        body = '\n'.join([
            f"{r['submitter_name']} asked the region to pay {r['payee_name']} ${r['net']:,.2f} ({rid}).",
            f"For: {r['event_name'] or 'program ' + r['program']} {r['event_code']}", '',
            *[f"  {ln['date']}  {ln['description']}  ${ln['amount']:,.2f}" for ln in r['lines']], '',
            f"Region policy asks the {FL.ROLES[role].lower()} to approve it before the treasurer pays.",
            f"Approve or reject (no password needed): {_link(rid, role, name, mail)}"])
        send_mail(f"Please approve funds request {rid}: {r['payee_name']} ${r['net']:,.2f}", body, mail)
    return [m for _, _, m in to]


def notify(rid, req, lines, n_files):
    """Email the treasurer about a new funds request (from the form, or an approved points redemption)."""
    body = '\n'.join([
        f"{req['submitter_name']} submitted funds request {rid}.", '',
        f"Event: {req['event_name'] or '(not one event)'} {req['event_code']}  Program: {req['program']}",
        f"Pay: {req['payee_name']}  by {req['method']}",
        f"Address: {req['payee_address']}" if req['payee_address'] else '', '',
        *[f"  {ln['date']}  {ln['category']}: {ln['description']}  ${ln['amount']:,.2f}" for ln in lines], '',
        f"Total ${req['total']:,.2f}" + (f"  less advances ${req['advances']:,.2f}  = ${req['net']:,.2f}" if req['advances'] else ''),
        f"Receipts: {n_files} file(s)" if n_files else 'Receipts: none attached',
        f"Notes: {req['notes']}" if req['notes'] else '', '',
        f"Review and mark paid: {request.url_root.rstrip('/')}{url_for('fr.detail', req_id=rid)}"])
    send_mail(f"Funds request {rid}: {req['payee_name']} ${req['net']:,.2f}", body)
    try:
        ask_approvals(rid)
    except Exception:
        current_app.logger.exception('asking for approvals on %s', rid)


@bp.route('/')
@login_required
def home():
    if (g := _guard()):
        return g
    status = request.args.get('status', 'Submitted')
    reqs = sorted(F.requests(), key=lambda r: r['id'], reverse=True)
    counts = {s: sum(1 for r in reqs if r['status'] == s) for s in ('Submitted', 'Paid', 'Rejected')}
    shown = reqs if status == 'All' else [r for r in reqs if r['status'] == status]
    a = Approvals()
    for r in shown:
        if r['status'] == 'Submitted':
            r['waiting'] = FL.waiting_on(a.items(r))
    return render_template('fr.html', **_ctx(view='list', reqs=shown, status=status, counts=counts,
                                             open_total=sum(r['net'] for r in reqs if r['status'] == 'Submitted')))


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    if (g := _guard()):
        return g
    events = _events()
    if request.method == 'GET':
        last = session.get('fr_submitter', {})
        return render_template('fr.html', **_ctx(view='new', events=events, programs=load_programs(), form={}, last=last,
                                                 rows=[{}] * 3))
    f = request.form
    errors = []
    lines = []
    for d, desc, cat, amt in zip(f.getlist('line_date'), f.getlist('line_desc'), f.getlist('line_cat'), f.getlist('line_amount')):
        if not (desc.strip() or amt.strip()):
            continue
        a = _money(amt)
        if a is None or a <= 0:
            errors.append(f'"{desc or "expense"}": the amount must be a number above zero.')
            continue
        if not desc.strip():
            errors.append('Every expense needs a description.')
            continue
        lines.append({'date': d, 'description': desc.strip(), 'category': cat if cat in F.CATEGORIES else 'Other', 'amount': a})
    if not lines:
        errors.append('Add at least one expense with an amount.')
    method = f.get('method')
    if method not in F.METHODS:
        errors.append('Choose how the funds should be paid.')
    ev = next((e for e in events if (e.get('custom_tag') or e.get('unique_code')) == f.get('event_code')), None)
    program = ev['program_code'] if ev else f.get('program', '')
    if not ev and not program:
        errors.append('Pick the event, or the program if this is not for one event.')
    advances = _money(f.get('advances') or 0)
    if advances is None or advances < 0:
        errors.append('Advances must be a number (0 if none).')
        advances = 0
    sub = {k: f.get(f'submitter_{k}', '').strip() for k in ('name', 'email', 'phone', 'address')}
    payee = {k: f.get(f'payee_{k}', '').strip() for k in ('name', 'email', 'phone', 'address')}
    if method == 'reimburse':
        payee = dict(sub)
    if not sub['name'] or not sub['email']:
        errors.append('Your name and email are required.')
    if not payee['name']:
        errors.append('Who should receive the funds?')
    if method == 'check' and not payee['address']:
        errors.append('A check needs the payee mailing address.')
    receipts = request.files.getlist('receipts')
    if errors:
        for e in errors:
            flash(e, 'danger')
        rows = [{'date': d, 'description': desc, 'category': c, 'amount': a}
                for d, desc, c, a in zip(f.getlist('line_date'), f.getlist('line_desc'), f.getlist('line_cat'), f.getlist('line_amount'))]
        return render_template('fr.html', **_ctx(view='new', events=events, programs=load_programs(), form=f, last={},
                                                 rows=rows or [{}])), 400
    total = round(sum(ln['amount'] for ln in lines), 2)
    req = {'event_code': f.get('event_code', '') if ev else '', 'event_name': ev['event_name'] if ev else '', 'program': program,
           **{f'submitter_{k}': v for k, v in sub.items()}, **{f'payee_{k}': v for k, v in payee.items()},
           'method': F.METHODS[method], 'total': total, 'advances': advances, 'net': round(total - advances, 2),
           'notes': f.get('notes', '').strip(), 'entered_by': sub['name']}
    rid = F.create(req, lines, receipts)
    session['who'] = sub['name']
    session['fr_submitter'] = sub
    n_files = sum(1 for r in receipts if r and r.filename)
    log_audit_event('FUNDS_REQUESTED', sub['name'], {'id': rid, 'payee': payee['name'], 'net': req['net'], 'event_code': req['event_code']})
    notify(rid, req, lines, n_files)
    flash(f'Submitted {rid}. The treasurer has been emailed.', 'success')
    return redirect(url_for('fr.detail', req_id=rid))


@bp.route('/<req_id>')
@login_required
def detail(req_id):
    if (g := _guard()):
        return g
    r = F.get(req_id)
    if not r:
        flash('Funds request not found.', 'warning')
        return redirect(url_for('fr.home'))
    items = Approvals().items(r) if r['status'] == 'Submitted' else []
    return render_template('fr.html', **_ctx(view='detail', r=r, today=date.today().isoformat(), items=items, ready=FL.ready(items),
                                             approvals=FL.parse(r.get('approvals')), roles=FL.ROLES))


def _approved(rid, role, who, note=''):
    """Record an approval; tell the treasurer once nothing else is missing."""
    r = F.get(rid)
    F.update(rid, approvals=FL.add(r.get('approvals'), role, who, date.today().isoformat(), note))
    log_audit_event('FUNDS_APPROVED', who, {'id': rid, 'role': role, 'note': note})
    r = F.get(rid)
    if FL.ready(Approvals().items(r)):
        send_mail(f"Funds request {rid} is ready to pay: {r['payee_name']} ${r['net']:,.2f}",
                  f"{FL.ROLES[role]} approval from {who}. Everything the policy asks for is in.\n\n"
                  f"{request.url_root.rstrip('/')}{url_for('fr.detail', req_id=rid)}")


@bp.route('/<req_id>/approve', methods=['POST'])
@login_required
def approve(req_id):
    """Record an approval given outside the app (in person, by text, in the board minutes)."""
    if (g := _guard()):
        return g
    F.refresh()
    r = F.get(req_id)
    role, note = request.form.get('role'), request.form.get('note', '').strip()
    name = request.form.get('name', '').strip()
    if not r or r['status'] != 'Submitted' or role not in FL.ROLES or not name:
        flash('Pick who approved it and enter their name.', 'danger')
        return redirect(url_for('fr.detail', req_id=req_id))
    if role == 'board' and not note:
        flash('Give the board meeting date or minutes for a board approval.', 'danger')
        return redirect(url_for('fr.detail', req_id=req_id))
    who = session.get('who') or name
    _approved(req_id, role, name, note if role == 'board' else f'recorded by {who}' + (f': {note}' if note else ''))
    flash(f'Approval recorded on {req_id}.', 'success')
    return redirect(url_for('fr.detail', req_id=req_id))


@bp.route('/<req_id>/ask', methods=['POST'])
@login_required
def ask(req_id):
    if (g := _guard()):
        return g
    sent = ask_approvals(req_id)
    flash(f"Asked {', '.join(sent)} again." if sent else 'Nobody to email: no director email on the annual plan, or no signers set.',
          'success' if sent else 'warning')
    return redirect(url_for('fr.detail', req_id=req_id))


@bp.route('/<req_id>/receipts', methods=['POST'])
@login_required
def receipts(req_id):
    if (g := _guard()):
        return g
    F.refresh()
    if not F.get(req_id):
        abort(404)
    n = F.add_receipts(req_id, request.files.getlist('receipts'))
    if n:
        log_audit_event('FUNDS_RECEIPTS', session.get('who') or 'Unknown', {'id': req_id, 'files': n})
    flash(f'Added {n} file(s).' if n else 'Choose a file first.', 'success' if n else 'warning')
    return redirect(url_for('fr.detail', req_id=req_id))


@bp.route('/approve/<token>', methods=['GET', 'POST'])
def approve_link(token):
    """The emailed approval link: the signed token stands in for the app password, for this one request and role."""
    if (g := _guard()):
        return g
    try:
        t = _serializer().loads(token, max_age=LINK_DAYS * 86400)
    except BadSignature:
        return render_template('fr.html', **_ctx(view='link', r=None, msg='This link has expired or is not valid. Ask the treasurer for a new one.'))
    F.refresh()
    r = F.get(t['id'])
    if not r:
        abort(404)
    done = next((a for a in FL.parse(r.get('approvals')) if a['role'] == t['role']), None)
    msg = (f"{r['id']} is already {r['status'].lower()}." if r['status'] != 'Submitted' else
           f"Already approved by {done['name']} on {done['date']}." if done else '')
    if request.method == 'POST' and not msg:
        who = t['name'] or t['email']
        if request.form.get('action') == 'reject':
            note = request.form.get('note', '').strip()
            if not note:
                flash('Say why, so the submitter knows.', 'danger')
                return redirect(request.url)
            F.close(r['id'], 'Rejected', closed_by=who, closed_note=f'Rejected by the {FL.ROLES[t["role"]].lower()}: {note}')
            log_audit_event('FUNDS_REJECTED', who, {'id': r['id'], 'note': note, 'via': 'email link'})
            send_mail(f"Funds request {r['id']} rejected by {who}", note)
            msg = "Rejected. The treasurer has been told."
        else:
            _approved(r['id'], t['role'], who, f"by email link ({t['email']})")
            msg = 'Approved. Thank you; the treasurer has it from here.'
    return render_template('fr.html', **_ctx(view='link', r=r, role=FL.ROLES[t['role']], name=t['name'], msg=msg))


@bp.route('/<req_id>/close', methods=['POST'])
@login_required
def close(req_id):
    if (g := _guard()):
        return g
    F.refresh()
    r = F.get(req_id)
    if not r:
        flash('Funds request not found.', 'warning')
        return redirect(url_for('fr.home'))
    who = request.form.get('entered_by', '').strip() or session.get('who', '') or 'Unknown'
    session['who'] = who
    action = request.form.get('action')
    if action == 'paid':
        if not FL.ready(Approvals().items(r)):
            flash('Not every approval is in yet; see the list on this page.', 'danger')
            return redirect(url_for('fr.detail', req_id=req_id))
        amount = _money(request.form.get('paid_amount') or r['net'])
        ref = request.form.get('paid_ref', '').strip()
        if amount is None or not ref:
            flash('Enter the check number or card used, and the amount.', 'danger')
            return redirect(url_for('fr.detail', req_id=req_id))
        F.close(req_id, 'Paid', paid_date=request.form.get('paid_date') or date.today().isoformat(), paid_ref=ref,
                paid_amount=amount, closed_by=who, closed_note=request.form.get('note', '').strip())
        log_audit_event('FUNDS_PAID', who, {'id': req_id, 'ref': ref, 'amount': amount})
        flash(f'{req_id} marked paid ({ref}).', 'success')
    elif action == 'reject':
        note = request.form.get('note', '').strip()
        if not note:
            flash('Say why it is rejected; the submitter will ask.', 'danger')
            return redirect(url_for('fr.detail', req_id=req_id))
        F.close(req_id, 'Rejected', closed_by=who, closed_note=note)
        log_audit_event('FUNDS_REJECTED', who, {'id': req_id, 'note': note})
        flash(f'{req_id} rejected.', 'warning')
    elif action == 'reopen':
        F.close(req_id, 'Submitted', paid_date='', paid_ref='', paid_amount='', closed_by=who, closed_note='')
        log_audit_event('FUNDS_REOPENED', who, {'id': req_id})
        flash(f'{req_id} is open again.', 'info')
    return redirect(url_for('fr.detail', req_id=req_id))
