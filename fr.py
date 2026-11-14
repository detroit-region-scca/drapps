"""Funds Requests: replaces the emailed Excel Funds Request Form. Records live in the Funds Requests sheet (fr_store).

A request is paid only once it has the approvals the region policies ask for (fr_logic). The chair or program
director and, over $2,500, the RE or ARE get an email with a link that approves without the app password. Their
addresses come only from the read-only officials directory (officials.py).
"""
import auth
import os
import secrets
from datetime import date

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

import bud as BUD
import bud_store as B
import fr_logic as FL
import fr_store as F
import officials as O
import plan_store as P
import shortlinks
from officer import current as officer_current, officer_required
from utils import load_events, load_programs, log_audit_event, login_required, send_mail, to_number

bp = Blueprint('fr', __name__, url_prefix='/funds')

ADDR = ('address', 'city')


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Money", who=session.get('who', ''),
                categories=F.CATEGORIES, methods=F.METHODS, officer=officer_current('pay'), **kw)


def _guard():
    if not F.enabled():
        flash('Money isn\'t set up on this server yet. Ask admin@drscca.org.', 'warning')
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


LINK_DAYS = shortlinks.DAYS
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

    def items(self, r):
        """What the request still needs (fr_logic.review), with a plain line where the payee or submitter is one of
        its usual approvers (fr_logic.who_approves)."""
        return FL.explain(self._items(r), approval_plan(r))

    def _items(self, r):
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
            st = B.status(b)
            status = 'approved' if st == 'no_vote' else st if st in ('approved', 'changed') else 'budget'
            expense = (b['totals'].get('budget') or {}).get('expense')
        if code:
            q = self.qbo.get(code) or {}
            same = [x for x in self.reqs if x['event_code'] == code and x['id'] != r['id']]
        else:
            if b and (program, year) not in self.other:
                self.other[(program, year)] = BUD.program_actuals(program, year) or {}
            q = self.other[(program, year)] if b else {}
            same = [x for x in self.reqs if not x['event_code'] and x['program'] == program and x['submitted'][:4] == year and x['id'] != r['id']]
        spent = (q.get('totals') or {}).get('expense') or 0.0
        spent += sum(x['net'] or 0 for x in same if x['status'] == 'Submitted')      # open requests count in full
        return FL.review(r, status, expense, round(spent, 2), scope=scope, board_vote=self.board_vote(r))


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='funds-approve')


def _link(rid, role, name, mail):
    token = _serializer().dumps({'id': rid, 'role': role, 'name': name, 'email': mail})
    return shortlinks.make(f"{request.url_root.rstrip('/')}{url_for('fr.approve_link', token=token)}", LINK_DAYS)


def _own_emails(name):
    """The sign-in addresses the officials list has for a name."""
    return [(o.get('email') or '').strip().lower() for o in O.all() if o.get('name') == name and o.get('email')]


def approval_plan(r):
    """Who may approve this request (fr_logic.who_approves), from the read-only officials list: never its payee or
    submitter. When that is the director, the RE or ARE approves in their place."""
    name, mail = O.director(r['program'])
    director = {'name': name, 'mail': mail, 'emails': [mail, O.director_email(r['program'])]} if name else None
    signers = [{'name': n, 'mail': m, 'emails': [m] + _own_emails(n)} for n, m in O.signers()]
    return FL.who_approves(r, director, signers)


def approvers(r):
    """Who gets the approval email for each approval item: {item key: [names]}, from the read-only officials list.
    An empty list means nobody would be emailed. An item whose label already says why nobody can approve is left out."""
    plan = approval_plan(r)
    signers = [n for n, m in O.signers()]
    out = {'chair': [x['name'] for x in plan['chair']], 'second': [x['name'] for x in plan['second']], 'budget': signers, 'overrun': signers}
    return {k: v for k, v in out.items() if v or k not in plan['note']}


def directors():
    """{program code: director name} for the request form."""
    return {p: O.director(p)[0] for p in dict(load_programs(), OPS='Operations') if O.director(p)[1]}


def ask_approvals(rid, roles=None):
    """Ask whoever still has to approve. The program director, and the RE and ARE as second signers over the limit,
    get an emailed link (at their address in the read-only officials list; approving is only possible from these
    links). Nobody is asked to approve money paid to themselves: the RE or ARE is asked in the director's place, and only
    the other of the RE and ARE as second signer when one of them is on the request (approval_plan). When the budget does not cover the request, it goes on the Board page as a motion instead: only a vote
    the board has taken, recorded there, counts. Returns who was asked (addresses, and "the board (motion ...)")."""
    r = F.get(rid)
    if not r or r['status'] != 'Submitted':
        return []
    a = Approvals()
    missing = {i['key'] for i in a.items(r) if not i['done']}
    need_board, no_budget = bool(missing & {'budget', 'overrun'}), 'budget' in missing
    missing &= set(roles or ('chair', 'second', 'board'))
    plan = approval_plan(r)
    to = [(role, x['name'], x['mail']) for role in ('chair', 'second') if role in missing for x in plan[role]]
    why = {'chair': 'Region policy asks the program director to approve each expense before the treasurer pays.' if not plan['stand_in'] else
                    'Region policy asks the program director to approve each expense before the treasurer pays. This one pays the '
                    'director, so the approval is asked of the RE or ARE instead.',
           'second': f'It is over ${FL.SECOND_SIGNER_LIMIT:,.0f}, so the bylaws ask the RE or ARE to approve it as second signer.'}
    from app import nice_date
    for role, name, mail in to:
        body = '\n'.join([
            f"{r['submitter_name']} asked the region to pay {r['payee_name'] or '(payee not given)'} ${r['net']:,.2f} ({rid}).",
            f"For: {r['event_name'] or 'program ' + r['program']} {r['event_code']}", '',
            *[f"  {nice_date(ln['date'])}  {ln['description']}  ${ln['amount']:,.2f}" for ln in r['lines']], '',
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
    from app import nice_date
    details = '\n'.join([f"{nice_date(ln['date'])}  {ln['description']}  ${ln['amount']:,.2f}" for ln in r['lines']] +
                        ([f"Notes from {r['submitter_name']}: {r['notes']}"] if r.get('notes') else []))
    # Made by the app, not proposed by anyone: say so, rather than putting it in the submitter's name.
    who = f"DRSCCA Apps (for {r['id']})"
    mid = M.propose(title[:200], details, r['net'], url_for('fr.detail', req_id=r['id']), who)
    log_audit_event('MOTION_PROPOSED', who, {'id': mid, 'funds_request': r['id'], 'automatic': True})
    return mid


SELF_NOTE = 'filed it as the program director'


def self_approve(rid, req=None):
    """The program director filed this request for their own program (by the address they signed in with, never by
    what was typed), and it does not pay them: filing it is the director's approval (user, 2026-11-13; Operations, whose
    approver is the treasurer, the same). Recorded as the chair approval, and the director's role group is told by
    email in place of an approval link. Every other check stays: the budget, the 10% overrun, the board and, over
    $2,500, the second signer. Returns True when it was approved here."""
    r = req or F.get(rid)          # the new request as filed, so the sheet is read only when the director filed it
    if not r or r.get('status', 'Submitted') != 'Submitted':
        return False
    filer = (r.get('filed_email') or '').strip().lower()
    name, group = O.director(r['program'])
    if not filer or filer != O.director_email(r['program']) or FL.paid_to(r, name, [filer]):
        return False
    if not F.add_approval(rid, 'chair', name, date.today().isoformat(), SELF_NOTE, once=True):
        return False
    log_audit_event('FUNDS_APPROVED', name, {'id': rid, 'role': 'chair', 'note': SELF_NOTE})
    tell_director(F.get(rid))
    return True


def self_filed(r):
    """The director approval came from the director filing it (self_approve)."""
    return any(a['role'] == 'chair' and a.get('note') == SELF_NOTE for a in FL.parse(r.get('approvals')))


def tell_director(r):
    """Tell the director's role group where their own request stands. It is "approved" only when nothing is missing;
    otherwise it says their director approval is in and what is still needed (user, 2026-11-13)."""
    name, group = O.director(r['program'])
    if not group:
        return
    still = FL.waiting_on(Approvals().items(r))
    program = load_programs().get(r['program'], 'Operations' if r['program'] == 'OPS' else r['program'])
    what = f"{r['id']} ({r['payee_name']}, ${r['net']:,.2f}, by {r['method'].lower()})"
    link = url_for('fr.detail', req_id=r['id'], _external=True)
    if still:
        subj = f"Funds request {r['id']} is waiting on {', '.join(still)}"
        body = (f"{what} is not approved yet. {name} filed it as the {program} director, so the director approval is in "
                f"(there is nothing to approve by email).\n\nStill needed before it is approved: {', '.join(still)}. "
                f"You will get another email when it is.\n\n{link}")
    else:
        subj = f"Funds request {r['id']} approved: {r['payee_name']} ${r['net']:,.2f}"
        body = (f"{what} is approved: everything the region policy asks for is in, so the treasurer can pay it. "
                f"{name} filed it as the {program} director, which counts as the director approval.\n\n{link}")
    send_mail(subj, body, group)


def mark_ready(rid, r=None):
    """Record the day a request first has everything (see fr_logic.ready_on). Returns True when it was just marked."""
    r = r or F.get(rid)
    if not r or FL.ready_on(r.get('approvals')) or not FL.ready(Approvals().items(r)):
        return False
    return bool(F.add_approval(rid, 'ready', 'DRSCCA Apps', date.today().isoformat(), once=True))


def announce_ready(rids):
    """Tell the treasurer about each of these open requests that now has everything (after a board vote)."""
    F.refresh()
    a = Approvals()
    for rid in rids:
        r = F.get(rid)
        if r and r['status'] == 'Submitted' and FL.ready(a.items(r)):
            if mark_ready(rid, r) and self_filed(r):
                tell_director(r)
            send_mail(f"Funds request {rid} is ready to pay: {r['payee_name']} ${r['net']:,.2f}",
                      f"The board's approval is recorded. Everything the policy asks for is in.\n\n"
                      f"{url_for('fr.detail', req_id=rid, _external=True)}")


def notify(rid, req, lines, n_files):
    """Email the treasurer about a new funds request (from the form, or an approved points redemption)."""
    from app import nice_date
    body = '\n'.join([
        f"{req['submitter_name']} submitted funds request {rid}.", '',
        f"Event: {req['event_name'] or '(not one event)'} {req['event_code']}  Program: {req['program']}",
        f"Pay: {req['payee_name']}  by {req['method']}",
        f"Address: {req['payee_address']}" if req['payee_address'] else '', '',
        *[f"  {nice_date(ln['date'])}  {ln['category']}: {ln['description']}  ${ln['amount']:,.2f}" for ln in lines], '',
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
                                                 rows=[{}] * 3, token=secrets.token_urlsafe(16)))
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
    if lines and advances > round(sum(ln['amount'] for ln in lines), 2) + 0.005:
        errors.append('Advances cannot be more than the total of the expenses.')
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
        BUD.files_dropped(receipts)
        rows = [{'date': d, 'description': desc, 'category': c, 'amount': a}
                for d, desc, c, a in zip(f.getlist('line_date'), f.getlist('line_desc'), f.getlist('line_cat'), f.getlist('line_amount'))]
        return render_template('fr.html', **_ctx(view='new', events=events, programs=dict(load_programs(), OPS='Operations'), directors=directors(), signers=[n for n, m in O.signers()], form=f, last={},
                                                 events_js=_events_js(events, f.get('event_code', '')),
                                                 rows=rows or [{}], token=f.get('token') or secrets.token_urlsafe(16))), 400
    total = round(sum(ln['amount'] for ln in lines), 2)
    req = {'event_code': f.get('event_code', '') if ev else '', 'event_name': ev['event_name'] if ev else '', 'program': program,
           **{f'submitter_{k}': v for k, v in sub.items()}, **{f'payee_{k}': v for k, v in payee.items()},
           'method': F.METHODS[method], 'total': total, 'advances': advances, 'net': round(total - advances, 2),
           'notes': f.get('notes', '').strip(), 'entered_by': session.get('who') or sub['name'],
           'notify': 'yes' if f.get('notify') else '',
           # Who filed it, from the sign-in and not from anything typed: this is what "my own request" goes by.
           'filed_email': (session.get('email') or '').strip().lower()}
    rid, again = F.create_once(req, lines, receipts, f.get('token', ''))
    if again:      # a double click, the back button, or a retry after an error: that page already filed this request
        flash(f'{rid} was already submitted from that page, so no second request was made.', 'info')
        return redirect(url_for('fr.detail', req_id=rid))
    session['fr_submitter'] = sub
    n_files = sum(1 for r in receipts if r and r.filename)
    log_audit_event('FUNDS_REQUESTED', sub['name'], {'id': rid, 'payee': payee['name'], 'net': req['net'], 'event_code': req['event_code']})
    # A director filing for their own program approves it by filing, unless it pays them (self_approve): done before
    # the approvals are asked for, so no link goes to the director.
    approved = self_approve(rid, req)
    notify(rid, req, lines, n_files)
    mark_ready(rid)
    flash(f'Submitted {rid}' + (', with your director approval' if approved else '') + '. The treasurer has been emailed.', 'success')
    return redirect(url_for('fr.detail', req_id=rid))


@bp.route('/<req_id>')
@login_required
def detail(req_id):
    if (g := _guard()):
        return g
    r = F.get(req_id)
    if not r:
        flash(f'There is no funds request "{req_id}". Pick one from the list.', 'warning')
        return redirect(url_for('fr.home'))
    items = Approvals().items(r) if r['status'] == 'Submitted' else []
    r['waiting'] = FL.waiting_on(items)      # the same status badge as the list
    return render_template('fr.html', **_ctx(view='detail', r=r, today=date.today().isoformat(), items=items, ready=FL.ready(items),
                                             can_delete=can_delete_request(r),
                                             approvals=FL.parse(r.get('approvals')), roles=FL.ROLES, approvers=approvers(r)))


def _approved(rid, role, who, note=''):
    """Record an approval; tell the treasurer once nothing else is missing."""
    # Added under the lock to what the row holds right now, and once per role: a second click changes nothing.
    if not F.add_approval(rid, role, who, date.today().isoformat(), note, once=True):
        return
    log_audit_event('FUNDS_APPROVED', who, {'id': rid, 'role': role, 'note': note})
    r = F.get(rid)
    if FL.ready(Approvals().items(r)):
        if mark_ready(rid, r) and self_filed(r):
            tell_director(r)
        send_mail(f"Funds request {rid} is ready to pay: {r['payee_name']} ${r['net']:,.2f}",
                  f"{FL.ROLES[role]} approval from {who}. Everything the policy asks for is in.\n\n"
                  f"{request.url_root.rstrip('/')}{url_for('fr.detail', req_id=rid)}")


@bp.route('/<req_id>/ask', methods=['POST'])
@login_required
def ask(req_id):
    if (g := _guard()):
        return g
    sent = ask_approvals(req_id)
    flash(f"Emailed {', '.join(sorted(set(sent)))}." if sent else 'Nobody to email. The approvals list on this page says who has to approve; '
          'if the program has no director in the officials list, ask the admin to add one.',
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
        # The receipt may have been the last thing missing: tell the treasurer, and the director when they filed it.
        F.refresh()
        r = F.get(req_id)
        if r and r['status'] == 'Submitted' and FL.ready(Approvals().items(r)) and mark_ready(req_id, r):
            send_mail(f"Funds request {req_id} is ready to pay: {r['payee_name']} ${r['net']:,.2f}",
                      f"The receipt is now attached. Everything the policy asks for is in.\n\n"
                      f"{url_for('fr.detail', req_id=req_id, _external=True)}")
            if self_filed(r):
                tell_director(r)
    flash(f'Added {n} file(s).' if n else 'Choose a file first.', 'success' if n else 'warning')
    return redirect(url_for('fr.detail', req_id=req_id))


def _signed_other(r, t):
    """True when this person already gave the other of the director (stand-in) and second-signer approvals, and the
    RE and ARE are both free to sign. With only one of them free, that one may give both: there is nobody else."""
    other = {'chair': 'second', 'second': 'chair'}.get(t.get('role'))
    plan = approval_plan(r)
    if not other or not plan['stand_in'] or len(plan['second']) < 2:
        return False
    return any(a['role'] == other and (a['name'] or '').strip().lower() == (t.get('name') or '').strip().lower()
               for a in FL.parse(r.get('approvals')))


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
        return render_template('fr.html', **_ctx(view='link', r=None, msg=f"{t['id']} no longer exists: it was deleted, so there is nothing to approve."))
    done = next((a for a in FL.parse(r.get('approvals')) if a['role'] == t['role']), None)
    from app import nice_date
    msg = (f"{r['id']} is already {r['status'].lower()}." if r['status'] != 'Submitted' else
           "Board approval is no longer confirmed from an email. The board votes on it (Board page, Request email vote), "
           "and the RE, ARE, secretary or treasurer records the result there." if t['role'] == 'board' else
           f"Already approved by {done['name']} on {nice_date(done['date'])}." if done else
           # Nobody approves money paid to themselves, whoever the link was sent to and whenever. The second signer may
           # not be on the request at all (fr_logic).
           (f"This money is paid to you, so you cannot approve {r['id']}. Someone else has to." if t['role'] == 'chair' else
            f"You are the payee or the submitter of {r['id']}, so you cannot sign it second. Someone else has to.")
           if (FL.paid_to if t['role'] == 'chair' else FL.on_request)(r, t.get('name'), [t.get('email')] + _own_emails(t.get('name'))) else
           # Standing in for the director and signing second are two signatures: two people, when two can sign.
           f"You already gave the other approval on {r['id']}. The other of the RE and ARE gives this one."
           if _signed_other(r, t) else '')
    if request.method == 'POST' and not msg:
        who = t['name'] or t['email']
        if request.form.get('action') == 'reject':
            note = request.form.get('note', '').strip()
            if not note:
                flash('Say why, so the submitter knows.', 'danger')
                return redirect(request.url)
            if F.close(r['id'], 'Rejected', expect='Submitted', closed_by=who,
                       closed_note=f'Rejected by the {FL.ROLES[t["role"]].lower()}: {note}'):
                log_audit_event('FUNDS_REJECTED', who, {'id': r['id'], 'note': note, 'via': 'email link'})
                send_mail(f"Funds request {r['id']} rejected by {who}", note)
                tell_submitter(r['id'])
                msg = "Rejected. The treasurer and the person who filed it have been told."
            else:
                msg = f"{r['id']} was just paid, rejected or deleted by someone else, so nothing was changed."
        else:
            note = f"by email link ({t['email']})"
            if t['role'] == 'chair' and approval_plan(r)['stand_in']:
                note += ', in place of the director, who is paid by it'
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
    back = redirect(url_for('fr.detail', req_id=req_id))
    # Paying and rejecting are for an open request, reopening for a closed one. Checked here and again under the lock
    # in fr_store, so a double click or two officers never close it twice.
    if (action in ('paid', 'reject')) != (r['status'] == 'Submitted'):
        flash(f"{req_id} is {'open' if r['status'] == 'Submitted' else r['status'].lower()} now, so nothing was changed.", 'warning')
        return back
    just_changed = f'{req_id} was just changed by someone else, so nothing was changed. Look at it again.'
    if action == 'paid':
        if not FL.ready(Approvals().items(r)):
            flash('Not every approval is in yet; see the list on this page.', 'danger')
            return back
        amount = _money(request.form.get('paid_amount') or r['net'])
        ref = request.form.get('paid_ref', '').strip()
        if amount is None or not ref:
            flash('Enter the check number or card used, and the amount.', 'danger')
            return back
        if amount < 0 or (amount == 0 and (r['net'] or 0) > 0):      # zero only when nothing is owed (advances covered it)
            flash('The amount paid must be above zero.', 'danger')
            return back
        if amount > FL.SECOND_SIGNER_LIMIT and not any(a['role'] == 'second' for a in FL.parse(r.get('approvals'))):
            flash(f'Over ${FL.SECOND_SIGNER_LIMIT:,.0f} needs the RE or ARE as second signer, and this request does not have that '
                  f'approval. Check the amount.', 'danger')
            return back
        if not F.close(req_id, 'Paid', expect='Submitted', paid_date=request.form.get('paid_date') or date.today().isoformat(), paid_ref=ref,
                       paid_amount=amount, closed_by=who, closed_note=request.form.get('note', '').strip()):
            flash(just_changed, 'warning')
            return back
        log_audit_event('FUNDS_PAID', who, {'id': req_id, 'ref': ref, 'amount': amount})
        tell_submitter(req_id)
        flash(f'{req_id} marked paid ({ref}).', 'success')
    elif action == 'reject':
        note = request.form.get('note', '').strip()
        if not note:
            flash('Say why it is rejected; the submitter will ask.', 'danger')
            return back
        if not F.close(req_id, 'Rejected', expect='Submitted', closed_by=who, closed_note=note):
            flash(just_changed, 'warning')
            return back
        log_audit_event('FUNDS_REJECTED', who, {'id': req_id, 'note': note})
        tell_submitter(req_id)
        flash(f'{req_id} rejected.', 'warning')
    elif action == 'reopen':
        if not F.close(req_id, 'Submitted', expect=r['status'], paid_date='', paid_ref='', paid_amount='', closed_by=who, closed_note=''):
            flash(just_changed, 'warning')
            return back
        log_audit_event('FUNDS_REOPENED', who, {'id': req_id})
        flash(f'{req_id} is open again.', 'info')
    return back


# ---------- Money received (cash and checks taken in) ----------

import received_store as R  # noqa: E402


def _owns_report(r):
    """Whoever reported it: by the signed-in address kept on the report, or by name for a report that has none."""
    me, name = (session.get('email') or '').strip().lower(), (session.get('who') or '').strip().lower()
    if not r:
        return False
    filed = (r.get('reported_email') or '').strip().lower()
    return bool(me) and filed == me if filed else bool(name) and (r.get('reported_by') or '').strip().lower() == name


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
        BUD.files_dropped(request.files.getlist('files'))
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
    from app import nice_date
    body = '\n'.join([
        f"{r['reported_by']} reported money received: {rid}.", '',
        f"Event: {r['event_name'] or '(not one event)'} {r['event_code']}  Program: {r['program']}",
        f"Received {nice_date(r['received'])}. Counted by {r['counted_by']}" + (f" and {r['second_counter']}." if r['second_counter'] else ' only.'),
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
    # Each step is checked against the report's status under the lock, so a double click is recorded once.
    same = f"{rid} is {'waiting for its deposit' if r['status'] == 'Received' else r['status'].lower()} now, so nothing was changed."
    if request.form.get('action') == 'reopen':
        if not R.update(rid, expect='Deposited', status='Received', deposit_date='', deposit_ref='', deposit_amount='', closed_by=who):
            flash(same, 'warning')
            return redirect(url_for('fr.in_detail', rid=rid))
        log_audit_event('MONEY_RECEIVED_REOPENED', who, {'id': rid})
        flash(f'{rid} is waiting for its deposit again.', 'info')
        return redirect(url_for('fr.in_detail', rid=rid))
    amount = _money(request.form.get('deposit_amount') or r['total'])
    if amount is None or amount <= 0:
        flash('The deposit amount must be a number above zero.', 'danger')
        return redirect(url_for('fr.in_detail', rid=rid))
    ref = request.form.get('deposit_ref', '').strip() or 'Bank deposit'
    if not R.update(rid, expect='Received', status='Deposited', deposit_date=request.form.get('deposit_date') or date.today().isoformat(),
                    deposit_ref=ref, deposit_amount=amount, closed_by=who):
        flash(same, 'warning')
        return redirect(url_for('fr.in_detail', rid=rid))
    log_audit_event('MONEY_DEPOSITED', who, {'id': rid, 'amount': amount, 'ref': ref})
    flash(f'{rid} marked deposited.' + (f' Note: ${amount:,.2f} deposited against ${r["total"]:,.2f} reported.' if abs(amount - r['total']) >= 0.005 else ''),
          'success' if abs(amount - r['total']) < 0.005 else 'warning')
    return redirect(url_for('fr.in_detail', rid=rid))


def can_delete_report(r):
    """Before it is deposited: whoever reported it, or the treasurer, secretary, RE or ARE."""
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
        flash('Only whoever reported it, or the treasurer, secretary, RE or ARE, can delete it.', 'warning')
        return redirect(url_for('fr.in_detail', rid=rid))
    who = session.get('who') or 'Unknown'
    gone = R.delete(rid, who)
    log_audit_event('MONEY_RECEIVED_DELETED', who, {'id': rid, 'total': gone['total'], 'cash': gone['cash'], 'event_code': gone['event_code'],
                                                     'received': gone['received'], 'reported_by': gone['reported_by'],
                                                     'lines': [{k: ln[k] for k in ('description', 'category', 'form', 'check_number', 'amount')} for ln in gone['lines']],
                                                     'files_url': gone['files_url']})
    flash(f'{rid} deleted.' + (' Its photos are still in Drive.' if gone['files_url'] else ''), 'info')
    return redirect(url_for('fr.in_list'))


def can_delete_request(r):
    """Before it is paid or rejected: whoever filed it, or the treasurer, secretary, RE or ARE."""
    return bool(r and r['status'] == 'Submitted' and (auth.owns_request(r) or officer_current('pay')))


@bp.route('/<req_id>/delete', methods=['POST'])
@login_required
def delete(req_id):
    if (g := _guard()):
        return g
    F.refresh()
    r = F.get(req_id)
    if not r:
        abort(404)
    if r['status'] != 'Submitted':
        flash('A paid or rejected request is part of the record. Reopen it first if it really has to go.', 'warning')
        return redirect(url_for('fr.detail', req_id=req_id))
    if not can_delete_request(r):
        flash('Only whoever filed it, or the treasurer, secretary, RE or ARE, can delete it.', 'warning')
        return redirect(url_for('fr.detail', req_id=req_id))
    import motions_store as M
    if M.enabled() and any(m['status'] == 'open' and (m.get('link') or '').rstrip('/').endswith('/' + req_id) for m in M.motions()):
        flash('An open board motion is about this request. Withdraw the motion first.', 'warning')
        return redirect(url_for('fr.detail', req_id=req_id))
    who = session.get('who') or 'Unknown'
    F.delete(req_id, who)
    log_audit_event('FUNDS_DELETED', who, {'id': req_id, 'payee': r['payee_name'], 'net': r['net'], 'event_code': r['event_code'],
                                           'submitter': r['submitter_name'], 'method': r['method'],
                                           'lines': [{k: ln[k] for k in ('date', 'description', 'category', 'amount')} for ln in r['lines']],
                                           'receipts_url': r['receipts_url'], 'approvals': r.get('approvals', '')})
    flash(f'{req_id} deleted. Its approval links no longer work.' + (' Its receipts are still in Drive.' if r['receipts_url'] else ''), 'info')
    return redirect(url_for('fr.home'))
