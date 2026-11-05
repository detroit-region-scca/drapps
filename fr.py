"""Funds Requests: replaces the emailed Excel Funds Request Form. Records live in the Funds Requests sheet (fr_store).

A request is paid only once it has the approvals the region policies ask for (fr_logic). The chair or program
director and, over $2,500, the RE or ARE get an email with a link that approves without the app password. Their
addresses come only from the read-only officials directory (officials.py).
"""
import auth
import os
from datetime import date

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

import bud as BUD
import bud_store as B
import fr_logic as FL
import fr_store as F
import officials as O
from officer import current as officer_current, officer_required
import plan_store as P
from utils import load_events, load_programs, log_audit_event, login_required, send_mail, to_number

bp = Blueprint('fr', __name__, url_prefix='/funds')

ADDR = ('address', 'city')


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Money", who=session.get('who', ''),
                categories=F.CATEGORIES, methods=F.METHODS, officer=officer_current('pay'), **kw)


def _guard():
    if not F.enabled():
        flash('Funds requests need the Google Sheets storage (STORAGE_BACKEND=sheets and FUNDS_SHEET_ID).', 'warning')
        return redirect(url_for('home'))
    return None


def _money(v):
    try:
        return round(to_number(str(v).replace(',', '').replace('$', '').strip() or 0), 2)
    except ValueError:
        return None


def _events():
    return sorted(load_events(), key=lambda e: e.get('date', ''), reverse=True)


def _events_js(events, keep=''):
    """The event picker's choices: this year's and last year's events (newest first), plus the one asked for."""
    since = str(date.today().year - 1)
    return [{'c': e.get('custom_tag') or e.get('unique_code'), 'd': e.get('date', ''), 'n': e.get('event_name', ''), 'p': e.get('program_code', '')}
            for e in events if e.get('date', '') >= since or keep in (e.get('custom_tag'), e.get('unique_code'))]


LINK_DAYS = 90
# Funds requests not tied to an event are paid against their program's budget from this year on.
PROGRAM_BUDGETS_FROM = '2027'


class Approvals:
    """Everything review() needs, loaded once per page."""

    def __init__(self):
        self.reqs = F.requests()
        self.budgets = B.budgets() if B.enabled() else {}
        self.qbo = B.qbo_actuals() if B.enabled() else {}
        self.plans = {}
        self.other = {}
        self._motions = None

    def motions(self):
        if self._motions is None:
            import motions_store as M
            self._motions = M.motions() if M.enabled() else []
        return self._motions

    def board_vote(self, r):
        m = FL.motion_for(r['id'], self.motions())
        return m if m and m['status'] == 'passed' else None

    def _plan(self, year):
        if year not in self.plans:
            self.plans[year] = P.plan(year) if P.enabled() else {}
        return self.plans[year]

    def director(self, r):
        """The program director from the officials directory (read-only), never from anything editable in the app."""
        return O.director(r['program'])

    def _other_spent(self, program, year):
        if (program, year) not in self.other:
            q = BUD.program_actuals(program, year)
            self.other[(program, year)] = ((q or {}).get('totals') or {}).get('expense') or 0.0
        return self.other[(program, year)]

    def items(self, r):
        """A request for an event is paid against the event's budget. One not tied to an event is paid against its
        program's budget for the year (Operations: its annual plan, which is its budget)."""
        code, year, program = r['event_code'], r['submitted'][:4], r['program']
        if code:
            b, scope = self.budgets.get(code), 'event'
        elif program == 'OPS':
            info = self._plan(year).get('OPS', {}).get('info', {})
            st = P.plan_status(dict(info, program='OPS'))
            status = st if st in ('approved', 'changed') else ('budget' if st else None)
            return FL.review(r, status, None, 0.0, scope='ops', board_vote=self.board_vote(r))
        elif year < PROGRAM_BUDGETS_FROM:
            # Before program budgets existed, a program's annual plan covered its spending outside events.
            return FL.review(r, 'approved' if self._plan(year).get(program, {}).get('lines') else None, None, 0.0, scope='ops',
                             board_vote=self.board_vote(r))
        else:
            b, scope = self.budgets.get(BUD.program_code(program, year)), 'program'
        status, expense = None, None
        if b:
            status = 'changed' if b.get('approved') and b.get('updated', '') > b['approved'] else 'approved' if b.get('approved') else 'budget'
            expense = (b['totals'].get('budget') or {}).get('expense')
        if code:
            spent = (self.qbo.get(code) or {}).get('totals', {}).get('expense') or 0.0
            spent += sum(x['net'] for x in self.reqs if x['event_code'] == code and x['status'] == 'Submitted' and x['id'] != r['id'])
        else:
            spent = self._other_spent(program, year) if b else 0.0
            spent += sum(x['net'] for x in self.reqs if not x['event_code'] and x['program'] == program and x['submitted'][:4] == year
                         and x['status'] == 'Submitted' and x['id'] != r['id'])
        return FL.review(r, status, expense, round(spent, 2), scope=scope, board_vote=self.board_vote(r))


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='funds-approve')


def _link(rid, role, name, mail):
    token = _serializer().dumps({'id': rid, 'role': role, 'name': name, 'email': mail})
    return f"{request.url_root.rstrip('/')}{url_for('fr.approve_link', token=token)}"


def approvers(r):
    """Who gets the approval email for each approval item: {item key: [names]}, from the read-only officials list.
    An empty list means nobody would be emailed."""
    name, mail = O.director(r['program'])
    signers = [n for n, m in O.signers()]
    return {'chair': [name] if mail else [], 'second': signers, 'budget': signers, 'overrun': signers}


def directors():
    """{program code: director name} for the request form."""
    return {p: O.director(p)[0] for p in dict(load_programs(), OPS='Operations') if O.director(p)[1]}


def ask_approvals(rid, roles=None):
    """Ask whoever still has to approve. The program director, and the RE and ARE as second signers over the limit,
    get an emailed link (at their address in the read-only officials list; approving is only possible from these
    links). When the budget does not cover the request, it goes on the Board page as a motion instead: only a vote
    the board has taken, recorded there, counts. Returns who was asked (addresses, and "the board (motion ...)")."""
    r = F.get(rid)
    if not r or r['status'] != 'Submitted':
        return []
    a = Approvals()
    missing = {i['key'] for i in a.items(r) if not i['done']}
    need_board, no_budget = bool(missing & {'budget', 'overrun'}), 'budget' in missing
    missing &= set(roles or ('chair', 'second', 'board'))
    to = []
    if 'chair' in missing:
        name, mail = a.director(r)
        if mail:
            to.append(('chair', name, mail))
    if 'second' in missing:
        to += [('second', n, m) for n, m in O.signers()]
    why = {'chair': 'Region policy asks the program director to approve each expense before the treasurer pays.',
           'second': f'It is over ${FL.SECOND_SIGNER_LIMIT:,.0f}, so the bylaws ask the RE or ARE to approve it as second signer.'}
    for role, name, mail in to:
        body = '\n'.join([
            f"{r['submitter_name']} asked the region to pay {r['payee_name'] or '(payee not given)'} ${r['net']:,.2f} ({rid}).",
            f"For: {r['event_name'] or 'program ' + r['program']} {r['event_code']}", '',
            *[f"  {ln['date']}  {ln['description']}  ${ln['amount']:,.2f}" for ln in r['lines']], '',
            why[role],
            f"Approve or reject (no password needed; the link only works for this request): {_link(rid, role, name, mail)}"])
        send_mail(f"Please approve funds request {rid}: {r['payee_name']} ${r['net']:,.2f}", body, mail)
    out = [m for _, _, m in to]
    if need_board and (roles is None or 'board' in roles):
        mid = board_motion(r, no_budget)
        if mid:
            out.append(f'the Board page (motion {mid})')
    return out


def board_motion(r, no_budget=True):
    """Put the request on the Board page as a motion, once. Returns its id."""
    import motions_store as M
    if not M.enabled():
        return None
    m = FL.motion_for(r['id'], M.motions())
    if m and m['status'] in ('open', 'passed'):
        return m['id']
    if m and m['status'] == 'withdrawn':
        return None     # taken off the board's list on purpose (paid against a budget instead): never put it back
    why = 'no approved budget' if no_budget else 'over its budget by more than 10%'
    title = f"Pay funds request {r['id']}: {r['payee_name']} ${r['net']:,.2f} for {r['event_name'] or 'program ' + r['program']} ({why})"
    details = '\n'.join([f"{ln['date']}  {ln['description']}  ${ln['amount']:,.2f}" for ln in r['lines']] +
                        ([f"Notes from {r['submitter_name']}: {r['notes']}"] if r.get('notes') else []))
    # Made by the app, not proposed by anyone: say so, rather than putting it in the submitter's name.
    who = f"DRSCCA Apps (for {r['id']})"
    mid = M.propose(title[:200], details, r['net'], url_for('fr.detail', req_id=r['id']), who)
    log_audit_event('MOTION_PROPOSED', who, {'id': mid, 'funds_request': r['id'], 'automatic': True})
    return mid


def announce_ready(rids):
    """Tell the treasurer about each of these open requests that now has everything (after a board vote)."""
    F.refresh()
    a = Approvals()
    for rid in rids:
        r = F.get(rid)
        if r and r['status'] == 'Submitted' and FL.ready(a.items(r)):
            send_mail(f"Funds request {rid} is ready to pay: {r['payee_name']} ${r['net']:,.2f}",
                      f"The board's approval is recorded. Everything the policy asks for is in.\n\n"
                      f"{url_for('fr.detail', req_id=rid, _external=True)}")


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
    every = F.requests()
    if auth.limited('funds'):
        every = [r for r in every if auth.owns_request(r)]
    this_year = str(date.today().year)
    years = sorted({r['submitted'][:4] for r in every if r['submitted'][:4].isdigit()} | {this_year}, reverse=True)
    year = request.args.get('year') if request.args.get('year') in years else this_year
    # Waiting requests show whatever year they were filed in: an open request is never out of view.
    reqs = sorted((r for r in every if r['submitted'].startswith(year) or r['status'] == 'Submitted'), key=lambda r: r['id'], reverse=True)
    counts = {s: sum(1 for r in reqs if r['status'] == s) for s in ('Submitted', 'Paid', 'Rejected')}
    shown = reqs if status == 'All' else [r for r in reqs if r['status'] == status]
    a = Approvals()
    for r in reqs:
        if r['status'] == 'Submitted':
            r['waiting'] = FL.waiting_on(a.items(r))
    return render_template('fr.html', **_ctx(view='list', reqs=shown, status=status, counts=counts, year=year, years=years,
                                             open_total=sum(r['net'] for r in reqs if r['status'] == 'Submitted'),
                                             n_ready=sum(1 for r in reqs if r['status'] == 'Submitted' and not r.get('waiting'))))


def _last_submitter():
    """The signed-in person's details from their most recent funds request, so the form is filled in for them."""
    me = (session.get('email') or '').lower()
    if not me:
        return {}
    mine = [r for r in F.requests() if (r.get('submitter_email') or '').lower() == me]
    if not mine:
        return {}
    r = max(mine, key=lambda r: (r.get('submitted') or '', r.get('id') or ''))
    return {k: r.get(k, '') for k in ('submitter_name', 'submitter_email', 'submitter_phone', 'submitter_address')}


@bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    if (g := _guard()):
        return g
    events = _events()
    if request.method == 'GET':
        last = session.get('fr_submitter') or _last_submitter()
        return render_template('fr.html', **_ctx(view='new', events=events, programs=dict(load_programs(), OPS='Operations'), directors=directors(), signers=[n for n, m in O.signers()], form={'program': request.args.get('program', '')}, last=last,
                                                 pick=request.args.get('event', ''), events_js=_events_js(events, request.args.get('event', '')),
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
        return render_template('fr.html', **_ctx(view='new', events=events, programs=dict(load_programs(), OPS='Operations'), directors=directors(), signers=[n for n, m in O.signers()], form=f, last={},
                                                 events_js=_events_js(events, f.get('event_code', '')),
                                                 rows=rows or [{}])), 400
    total = round(sum(ln['amount'] for ln in lines), 2)
    req = {'event_code': f.get('event_code', '') if ev else '', 'event_name': ev['event_name'] if ev else '', 'program': program,
           **{f'submitter_{k}': v for k, v in sub.items()}, **{f'payee_{k}': v for k, v in payee.items()},
           'method': F.METHODS[method], 'total': total, 'advances': advances, 'net': round(total - advances, 2),
           'notes': f.get('notes', '').strip(), 'entered_by': session.get('who') or sub['name'],
           'notify': 'yes' if f.get('notify') else ''}
    # A director filing a request for their own program has approved it: they are signed in as themselves
    # (emailed code or Google), so their filing is their approval.
    me = (session.get('email') or '').lower()
    if me and me == O.director_email(program):
        req['approvals'] = FL.add('', 'chair', O.director(program)[0] or sub['name'], date.today().isoformat(), 'filed it themselves')
    rid = F.create(req, lines, receipts)
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
                                             approvals=FL.parse(r.get('approvals')), roles=FL.ROLES, approvers=approvers(r)))


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


@bp.route('/<req_id>/ask', methods=['POST'])
@login_required
def ask(req_id):
    if (g := _guard()):
        return g
    sent = ask_approvals(req_id)
    flash(f"Emailed {', '.join(sorted(set(sent)))}." if sent else 'Nobody to email: this program has no director in the officials list. Ask the admin to add one.',
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
           "Board approval is no longer confirmed from an email. The board votes on it (Board page, Request email vote), "
           "and the RE, ARE or treasurer records the result there." if t['role'] == 'board' else
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
            tell_submitter(r['id'])
            msg = "Rejected. The treasurer and the person who filed it have been told."
        else:
            note = f"by email link ({t['email']})"
            _approved(r['id'], t['role'], who, note)
            msg = 'Approved. Thank you; the treasurer has it from here.'
    return render_template('fr.html', **_ctx(view='link', r=r, role=FL.ROLES[t['role']], role_key=t['role'], name=t['name'], msg=msg))


def tell_submitter(req_id):
    """Email whoever filed the request that it was paid or rejected, if they ticked "Email me" on the form."""
    F.refresh()
    r = F.get(req_id)
    msg = r and FL.closed_message(r, url_for('fr.detail', req_id=req_id, _external=True))
    if msg:
        send_mail(*msg, r['submitter_email'])


@bp.route('/<req_id>/close', methods=['POST'])
@login_required
@officer_required(perm='pay')
def close(req_id):
    if (g := _guard()):
        return g
    F.refresh()
    r = F.get(req_id)
    if not r:
        flash('Funds request not found.', 'warning')
        return redirect(url_for('fr.home'))
    who = officer_current('pay')['name']      # signed in as themselves, with the pay permission
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
        tell_submitter(req_id)
        flash(f'{req_id} marked paid ({ref}).', 'success')
    elif action == 'reject':
        note = request.form.get('note', '').strip()
        if not note:
            flash('Say why it is rejected; the submitter will ask.', 'danger')
            return redirect(url_for('fr.detail', req_id=req_id))
        F.close(req_id, 'Rejected', closed_by=who, closed_note=note)
        log_audit_event('FUNDS_REJECTED', who, {'id': req_id, 'note': note})
        tell_submitter(req_id)
        flash(f'{req_id} rejected.', 'warning')
    elif action == 'reopen':
        F.close(req_id, 'Submitted', paid_date='', paid_ref='', paid_amount='', closed_by=who, closed_note='')
        log_audit_event('FUNDS_REOPENED', who, {'id': req_id})
        flash(f'{req_id} is open again.', 'info')
    return redirect(url_for('fr.detail', req_id=req_id))


# ---------- Money received (cash and checks taken in) ----------

import received_store as R  # noqa: E402


def _owns_report(r):
    me, name = (session.get('email') or '').lower(), (session.get('who') or '').lower()
    return bool(r and ((r.get('reported_email') or '').lower() == me or (r.get('reported_by') or '').lower() == name))


@bp.route('/in')
@login_required
def in_list():
    if (g := _guard()):
        return g
    status = request.args.get('status', 'Received')
    every = R.reports()
    if auth.limited('funds'):
        every = [r for r in every if _owns_report(r)]
    this_year = str(date.today().year)
    years = sorted({r['received'][:4] for r in every if r['received'][:4].isdigit()} | {this_year}, reverse=True)
    year = request.args.get('year') if request.args.get('year') in years else this_year
    reps = sorted((r for r in every if r['received'].startswith(year) or r['status'] == 'Received'), key=lambda r: r['id'], reverse=True)
    counts = {s: sum(1 for r in reps if r['status'] == s) for s in ('Received', 'Deposited')}
    shown = reps if status == 'All' else [r for r in reps if r['status'] == status]
    for r in reps:
        r['single'] = R.single_count(r)
    return render_template('fr.html', **_ctx(view='in_list', reps=shown, status=status, counts=counts, year=year, years=years,
                                             open_total=sum(r['total'] for r in reps if r['status'] == 'Received'),
                                             open_cash=sum(r['cash'] for r in reps if r['status'] == 'Received')))


@bp.route('/in/new', methods=['GET', 'POST'])
@login_required
def in_new():
    if (g := _guard()):
        return g
    events = _events()
    progs = dict(load_programs(), OPS='Operations')
    def page(form, rows, status=200):
        return render_template('fr.html', **_ctx(view='in_new', events=events, programs=progs, form=form,
                                                 events_js=_events_js(events, form.get('event_code', '')), rows=rows,
                                                 rcats=R.CATEGORIES, forms=R.FORMS, today=date.today().isoformat())), status
    if request.method == 'GET':
        return page({'program': request.args.get('program', ''), 'event_code': request.args.get('event', ''),
                     'counted_by': session.get('who', ''), 'held_by': session.get('who', '')}, [{'form': 'Cash'}, {'form': 'Check'}])
    f = request.form
    errors, lines = [], []
    rows = [{'description': d, 'category': c, 'form': m, 'check_number': n, 'amount': a}
            for d, c, m, n, a in zip(f.getlist('line_desc'), f.getlist('line_cat'), f.getlist('line_form'), f.getlist('line_check'), f.getlist('line_amount'))]
    for ln in rows:
        if not (ln['description'].strip() or ln['amount'].strip()):
            continue
        a = _money(ln['amount'])
        if a is None or a <= 0:
            errors.append(f'"{ln["description"] or "line"}": the amount must be a number above zero.')
            continue
        lines.append({'description': ln['description'].strip() or ln['category'], 'category': ln['category'] if ln['category'] in R.CATEGORIES else 'Other',
                      'form': ln['form'] if ln['form'] in R.FORMS else 'Cash',
                      'check_number': ln['check_number'].strip() if ln['form'] == 'Check' else '', 'amount': a})
    if not lines:
        errors.append('Add at least one line with an amount.')
    ev = next((e for e in events if (e.get('custom_tag') or e.get('unique_code')) == f.get('event_code')), None)
    program = ev['program_code'] if ev else f.get('program', '')
    if not ev and not program:
        errors.append('Pick the event, or the program if this is not from one event.')
    received = f.get('received', '').strip()
    try:
        if date.fromisoformat(received) > date.today():
            errors.append('The date received cannot be in the future.')
    except ValueError:
        errors.append('Enter the date the money was received.')
    counted, second, held = (f.get(k, '').strip() for k in ('counted_by', 'second_counter', 'held_by'))
    if not counted:
        errors.append('Who counted it?')
    if second and second.lower() == counted.lower():
        errors.append('The second counter has to be a different person.')
    if not held:
        errors.append('Who has the money now?')
    if errors:
        for e in errors:
            flash(e, 'danger')
        return page(f, rows or [{}], 400)
    rep = {'received': received, 'event_code': f.get('event_code', '') if ev else '', 'event_name': ev['event_name'] if ev else '',
           'program': program, 'reported_by': session.get('who', ''), 'reported_email': session.get('email', ''),
           'counted_by': counted, 'second_counter': second, 'held_by': held, 'notes': f.get('notes', '').strip()}
    rid = R.create(rep, lines, request.files.getlist('files'))
    r = R.get(rid)
    log_audit_event('MONEY_RECEIVED', rep['reported_by'] or 'Unknown', {'id': rid, 'total': r['total'], 'cash': r['cash'], 'event_code': rep['event_code']})
    notify_received(rid)
    flash(f'Reported {rid}. The treasurer has been emailed.', 'success')
    return redirect(url_for('fr.in_detail', rid=rid))


def notify_received(rid):
    """Email the treasurer (and the program's group) about money taken in."""
    r = R.get(rid)
    body = '\n'.join([
        f"{r['reported_by']} reported money received: {rid}.", '',
        f"Event: {r['event_name'] or '(not one event)'} {r['event_code']}  Program: {r['program']}",
        f"Received {r['received']}. Counted by {r['counted_by']}" + (f" and {r['second_counter']}." if r['second_counter'] else ' only.'),
        f"Held by {r['held_by']} until it is deposited.", '',
        *[f"  {ln['form']}{(' #' + ln['check_number']) if ln['check_number'] else ''}  {ln['category']}: {ln['description']}  ${ln['amount']:,.2f}"
          for ln in r['lines']], '',
        f"Cash ${r['cash']:,.2f}  Checks ${r['checks']:,.2f}  Total ${r['total']:,.2f}",
        'Counted by one person only.' if R.single_count(r) else '',
        f"Notes: {r['notes']}" if r['notes'] else '', '',
        f"{request.url_root.rstrip('/')}{url_for('fr.in_detail', rid=rid)}"])
    group = O.director(r['program'])[1]
    send_mail(f"Money received {rid}: ${r['total']:,.2f} {r['event_name'] or r['program']}", body, cc=group or None)


@bp.route('/in/<rid>')
@login_required
def in_detail(rid):
    if (g := _guard()):
        return g
    r = R.get(rid)
    if not r or (auth.limited('funds') and not _owns_report(r)):
        flash('Not found.', 'warning')
        return redirect(url_for('fr.in_list'))
    return render_template('fr.html', **_ctx(view='in_detail', r=r, single=R.single_count(r), today=date.today().isoformat(),
                                             can_delete=can_delete_report(r)))


@bp.route('/in/<rid>/files', methods=['POST'])
@login_required
def in_files(rid):
    if (g := _guard()):
        return g
    R.refresh()
    if not R.get(rid):
        abort(404)
    n = R.add_files(rid, request.files.getlist('files'))
    if n:
        log_audit_event('MONEY_RECEIVED_FILES', session.get('who') or 'Unknown', {'id': rid, 'files': n})
    flash(f'Added {n} file(s).' if n else 'Choose a file first.', 'success' if n else 'warning')
    return redirect(url_for('fr.in_detail', rid=rid))


@bp.route('/in/<rid>/deposit', methods=['POST'])
@login_required
@officer_required(perm='pay')
def in_deposit(rid):
    if (g := _guard()):
        return g
    R.refresh()
    r = R.get(rid)
    if not r:
        abort(404)
    who = officer_current('pay')['name']
    if request.form.get('action') == 'reopen':
        R.update(rid, status='Received', deposit_date='', deposit_ref='', deposit_amount='', closed_by=who)
        log_audit_event('MONEY_RECEIVED_REOPENED', who, {'id': rid})
        flash(f'{rid} is waiting for its deposit again.', 'info')
        return redirect(url_for('fr.in_detail', rid=rid))
    amount = _money(request.form.get('deposit_amount') or r['total'])
    if amount is None:
        flash('The deposit amount must be a number.', 'danger')
        return redirect(url_for('fr.in_detail', rid=rid))
    ref = request.form.get('deposit_ref', '').strip() or 'Bank deposit'
    R.update(rid, status='Deposited', deposit_date=request.form.get('deposit_date') or date.today().isoformat(), deposit_ref=ref,
             deposit_amount=amount, closed_by=who)
    log_audit_event('MONEY_DEPOSITED', who, {'id': rid, 'amount': amount, 'ref': ref})
    flash(f'{rid} marked deposited.' + (f' Note: ${amount:,.2f} deposited against ${r["total"]:,.2f} reported.' if abs(amount - r['total']) >= 0.005 else ''),
          'success' if abs(amount - r['total']) < 0.005 else 'warning')
    return redirect(url_for('fr.in_detail', rid=rid))


def can_delete_report(r):
    """Before it is deposited: whoever reported it, or the treasurer, RE or ARE."""
    return bool(r and r['status'] == 'Received' and (_owns_report(r) or officer_current('pay')))


@bp.route('/in/<rid>/delete', methods=['POST'])
@login_required
def in_delete(rid):
    if (g := _guard()):
        return g
    R.refresh()
    r = R.get(rid)
    if not r:
        abort(404)
    if r['status'] != 'Received':
        flash('A deposited report is part of the books. Mark it not deposited first if it really has to go.', 'warning')
        return redirect(url_for('fr.in_detail', rid=rid))
    if not can_delete_report(r):
        flash('Only whoever reported it, or the treasurer, RE or ARE, can delete it.', 'warning')
        return redirect(url_for('fr.in_detail', rid=rid))
    gone = R.delete(rid)
    who = session.get('who') or 'Unknown'
    log_audit_event('MONEY_RECEIVED_DELETED', who, {'id': rid, 'total': gone['total'], 'cash': gone['cash'], 'event_code': gone['event_code'],
                                                     'received': gone['received'], 'reported_by': gone['reported_by'],
                                                     'lines': [{k: ln[k] for k in ('description', 'category', 'form', 'check_number', 'amount')} for ln in gone['lines']],
                                                     'files_url': gone['files_url']})
    flash(f'{rid} deleted.' + (' Its photos are still in Drive.' if gone['files_url'] else ''), 'info')
    return redirect(url_for('fr.in_list'))
