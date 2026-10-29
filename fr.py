"""Funds Requests: replaces the emailed Excel Funds Request Form. Records live in the Funds Requests sheet (fr_store)."""
import os
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import fr_store as F
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


@bp.route('/')
@login_required
def home():
    if (g := _guard()):
        return g
    status = request.args.get('status', 'Submitted')
    reqs = sorted(F.requests(), key=lambda r: r['id'], reverse=True)
    counts = {s: sum(1 for r in reqs if r['status'] == s) for s in ('Submitted', 'Paid', 'Rejected')}
    shown = reqs if status == 'All' else [r for r in reqs if r['status'] == status]
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
    return render_template('fr.html', **_ctx(view='detail', r=r, today=date.today().isoformat()))


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
