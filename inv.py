"""Invoices: program directors bill sponsors, event partners and others in the region's name. Each invoice gets a
number, a professional PDF (kept in the shared drive) and a row in the DRSCCA Invoices sheet (inv_store). The
director sends the PDF to the customer; the treasurer marks it paid when the money is deposited.
"""
import os
from datetime import date

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

import inv_store as I
import officials as O
from utils import load_events, load_programs, log_audit_event, login_required, send_mail

bp = Blueprint('inv', __name__, url_prefix='/invoices')


def _programs():
    return dict(load_programs(), OPS='Operations')


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Invoices", programs=_programs(), **kw)


def _guard():
    if not I.enabled():
        flash('Invoices need the Google Sheets storage (STORAGE_BACKEND=sheets and INVOICES_SHEET_ID).', 'warning')
        return redirect(url_for('home'))
    return None


def _money(v):
    try:
        return round(float(str(v).replace(',', '').replace('$', '').strip()), 2)
    except ValueError:
        return None


def _events():
    since = str(date.today().year - 1)
    return [{'c': e.get('custom_tag') or e.get('unique_code'), 'd': e.get('date', ''), 'n': e.get('event_name', ''), 'p': e.get('program_code', '')}
            for e in sorted(load_events(), key=lambda e: e.get('date', ''), reverse=True) if e.get('date', '') >= since]


def _full(inv):
    """The invoice with what the PDF prints beside the stored fields."""
    return dict(inv, program_name=_programs().get(inv['program'], inv['program']))


@bp.route('/')
@login_required
def home():
    if (g := _guard()):
        return g
    this_year = str(date.today().year)
    rows = I.invoices()
    years = sorted({r['created'][:4] for r in rows} | {this_year}, reverse=True)
    year = request.args.get('year') if request.args.get('year') in years else this_year
    today = date.today().isoformat()
    shown = [r for r in rows if r['created'].startswith(year) or r['status'] == 'Open']
    open_ = [r for r in rows if r['status'] == 'Open']
    return render_template('inv.html', **_ctx(view='list', rows=shown, year=year, years=years, today=today,
                                              open_total=sum(r['total'] or 0 for r in open_),
                                              overdue=[r for r in open_ if r['due'] < today]))


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    if (g := _guard()):
        return g
    if request.method == 'GET':
        return render_template('inv.html', **_ctx(view='new', form={'program': request.args.get('program', ''), 'event_code': request.args.get('event', '')},
                                                  events=_events(), rows=[{}] * 2, due=''))
    f = request.form
    errors, lines = [], []
    for desc, qty, price in zip(f.getlist('desc'), f.getlist('qty'), f.getlist('price')):
        if not (desc.strip() or price.strip()):
            continue
        q, p = _money(qty or '1'), _money(price)
        if not desc.strip() or p is None or p <= 0 or not q or q <= 0:
            errors.append(f'"{desc or price}": each line needs a description, a quantity and a price above zero.')
            continue
        lines.append({'description': desc.strip(), 'quantity': q, 'unit_price': p})
    if not lines:
        errors.append('Add at least one line with a price.')
    program = f.get('program', '')
    if program not in _programs():
        errors.append('Pick the program.')
    if not f.get('bill_name', '').strip():
        errors.append('Who is the invoice to?')
    ev = next((e for e in load_events() if (e.get('custom_tag') or e.get('unique_code')) == f.get('event_code')), None)
    if errors:
        for e in errors:
            flash(e, 'danger')
        rows = [{'description': d, 'quantity': q, 'unit_price': p} for d, q, p in zip(f.getlist('desc'), f.getlist('qty'), f.getlist('price'))]
        return render_template('inv.html', **_ctx(view='new', form=f, events=_events(), rows=rows or [{}], due=f.get('due', ''))), 400
    who = session.get('who', '') or 'Unknown'
    inv = I.create({'program': program, 'event_code': f.get('event_code', '') if ev else '', 'event_name': ev['event_name'] if ev else '',
                    'bill_name': f['bill_name'].strip(), 'bill_contact': f.get('bill_contact', '').strip(), 'bill_email': f.get('bill_email', '').strip(),
                    'bill_address': f.get('bill_address', '').strip(), 'due': f.get('due', ''), 'notes': f.get('notes', '').strip(),
                    'created_by': who}, lines)
    pdf = I.pdf_bytes(_full(inv))
    try:
        I.update(inv['id'], pdf_url=I.save_pdf(inv, pdf))
    except Exception as e:      # the invoice is recorded; the PDF can always be made again from the record
        flash(f'Saved, but the PDF could not be stored in Drive ({e}). Download it below.', 'warning')
    log_audit_event('INVOICE_CREATED', who, {'id': inv['id'], 'bill_name': inv['bill_name'], 'total': inv['total'], 'program': program,
                                             'event_code': inv['event_code']})
    me = f.get('my_email', '').strip()
    body = (f"{who} created invoice {inv['id']} for {inv['bill_name']}: {I.money(inv['total'])}, due {inv['due']}.\n"
            f"{(inv['event_name'] + ' ' + inv['event_code']) if inv['event_name'] else 'Program ' + program}\n\n"
            f"The PDF is attached. Send it to the customer from your own email.\n"
            f"{request.url_root.rstrip('/')}{url_for('inv.detail', inv_id=inv['id'])}")
    send_mail(f"Invoice {inv['id']}: {inv['bill_name']} {I.money(inv['total'])}", body, me or None,
              attachments=[(f"{inv['id']}.pdf", pdf, 'application/pdf')], cc=os.environ.get('NOTIFICATION_EMAIL') if me else None)
    flash(f"Invoice {inv['id']} created. Download the PDF and send it to {inv['bill_name']}." + (' A copy is in your email.' if me else ''), 'success')
    return redirect(url_for('inv.detail', inv_id=inv['id']))


@bp.route('/<inv_id>')
@login_required
def detail(inv_id):
    if (g := _guard()):
        return g
    inv = I.get(inv_id)
    if not inv:
        abort(404)
    return render_template('inv.html', **_ctx(view='detail', inv=_full(inv), today=date.today().isoformat()))


@bp.route('/<inv_id>.pdf')
@login_required
def pdf(inv_id):
    if (g := _guard()):
        return g
    inv = I.get(inv_id)
    if not inv:
        abort(404)
    return Response(I.pdf_bytes(_full(inv)), mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="{inv_id}.pdf"'})


@bp.route('/<inv_id>/close', methods=['POST'])
@login_required
def close(inv_id):
    if (g := _guard()):
        return g
    I.refresh()
    inv = I.get(inv_id)
    if not inv:
        abort(404)
    who = session.get('who', '') or 'Unknown'
    action = request.form.get('action')
    if action == 'paid':
        amount = _money(request.form.get('paid_amount') or inv['total'])
        I.update(inv_id, status='Paid', paid_date=request.form.get('paid_date') or date.today().isoformat(),
                 paid_ref=request.form.get('paid_ref', '').strip(), paid_amount=amount, closed_by=who)
        log_audit_event('INVOICE_PAID', who, {'id': inv_id, 'amount': amount, 'ref': request.form.get('paid_ref', '').strip()})
        flash(f'{inv_id} marked paid.', 'success')
    elif action == 'void':
        note = request.form.get('note', '').strip()
        if not note:
            flash('Say why it is void.', 'danger')
            return redirect(url_for('inv.detail', inv_id=inv_id))
        I.update(inv_id, status='Void', closed_by=who, closed_note=note)
        log_audit_event('INVOICE_VOID', who, {'id': inv_id, 'note': note})
        flash(f'{inv_id} voided.', 'warning')
    elif action == 'reopen':
        I.update(inv_id, status='Open', paid_date='', paid_ref='', paid_amount='', closed_by='', closed_note='')
        log_audit_event('INVOICE_REOPENED', who, {'id': inv_id})
    return redirect(url_for('inv.detail', inv_id=inv_id))


DELETE_DAYS = 7


def _delete_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='invoice-delete')


def _treasurer_email():
    return next((o['email'] for o in O.all() if o.get('role') == 'Treasurer' and o.get('email')), os.environ.get('NOTIFICATION_EMAIL', ''))


@bp.route('/<inv_id>/delete', methods=['POST'])
@login_required
def delete_request(inv_id):
    """Ask to delete an invoice. Nothing is deleted here: the treasurer gets an email with a link to confirm."""
    if (g := _guard()):
        return g
    inv = I.get(inv_id)
    if not inv:
        abort(404)
    if inv['status'] == 'Paid':
        flash('A paid invoice cannot be deleted. Reopen it first if the payment was recorded by mistake.', 'warning')
        return redirect(url_for('inv.detail', inv_id=inv_id))
    why = request.form.get('note', '').strip()
    if not why:
        flash('Say why it should be deleted.', 'danger')
        return redirect(url_for('inv.detail', inv_id=inv_id))
    who = session.get('who', '') or 'Unknown'
    token = _delete_serializer().dumps({'id': inv_id, 'by': who, 'why': why})
    link = f"{request.url_root.rstrip('/')}{url_for('inv.delete_confirm', token=token)}"
    send_mail(f"Confirm deleting invoice {inv_id}: {inv['bill_name']} {I.money(inv['total'])}",
              f"{who} asked to delete invoice {inv_id} ({inv['bill_name']}, {I.money(inv['total'])}, {inv['status'].lower()}).\n"
              f"Why: {why}\n\nTo delete it, open this link (good for {DELETE_DAYS} days) and confirm:\n{link}\n\n"
              f"If it should stay, ignore this email.", _treasurer_email())
    log_audit_event('INVOICE_DELETE_REQUESTED', who, {'id': inv_id, 'why': why})
    flash(f'The treasurer has been asked to confirm deleting {inv_id}.', 'info')
    return redirect(url_for('inv.detail', inv_id=inv_id))


@bp.route('/delete/<token>', methods=['GET', 'POST'])
def delete_confirm(token):
    """The treasurer's emailed link: the signed token stands in for the app password, for this one invoice."""
    if (g := _guard()):
        return g
    try:
        t = _delete_serializer().loads(token, max_age=DELETE_DAYS * 86400)
    except BadSignature:
        return render_template('inv.html', **_ctx(view='delete', inv=None, msg='This link has expired or is not valid. Ask for the delete again.'))
    I.refresh()
    inv = I.get(t['id'])
    msg = '' if inv else f"{t['id']} is already gone."
    if inv and inv['status'] == 'Paid':
        msg = f"{t['id']} has been marked paid since, so it was not deleted."
    if request.method == 'POST' and not msg:
        I.delete(t['id'])
        log_audit_event('INVOICE_DELETED', 'Treasurer (email link)', {'id': t['id'], 'bill_name': inv['bill_name'], 'total': inv['total'],
                                                                      'asked_by': t['by'], 'why': t['why']})
        msg = f"{t['id']} is deleted. Its PDF is in the Drive trash if it is ever needed."
        inv = None
    return render_template('inv.html', **_ctx(view='delete', inv=inv, t=t, msg=msg))


def open_counts():
    """For the Treasurer page: (open, overdue, open total)."""
    if not I.enabled():
        return None
    today = date.today().isoformat()
    o = [r for r in I.invoices() if r['status'] == 'Open']
    return len(o), sum(1 for r in o if r['due'] < today), sum(r['total'] or 0 for r in o)
