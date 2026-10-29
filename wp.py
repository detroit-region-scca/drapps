"""Worker Points pages. Everything is read from and written to the ledger Google Sheet (wp_store)."""
import os
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import fr_store as F
import wp_logic as L
import wp_store as S
from fr import notify as fr_notify
from utils import load_events, load_programs, log_audit_event, login_required

bp = Blueprint('wp', __name__, url_prefix='/wp')


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Worker Points", enabled=S.enabled(), point_value=L.POINT_VALUE,
                who=session.get('who', ''), **kw)


def _who():
    """Who is entering this, from the form; remembered for the rest of the login so it is typed once."""
    who = request.form.get('entered_by', '').strip() or session.get('who', '')
    if who:
        session['who'] = who
    return who or 'Unknown'


def _events_by_code():
    out = {}
    for e in load_events():
        for c in (e.get('unique_code'), e.get('custom_tag')):
            if c:
                out[c] = e
    return out


def _guard():
    if not S.enabled():
        flash('Worker points need the Google Sheets storage (STORAGE_BACKEND=sheets and LEDGER_SHEET_ID).', 'warning')
        return redirect(url_for('home'))
    return None


def _month(value):
    """'YYYY-MM' from a form or query string; anything unreadable means this month."""
    try:
        y, mo = map(int, (value or '').split('-'))
        date(y, mo, 1)
    except ValueError:
        y, mo = date.today().year, date.today().month
    return f'{y:04d}-{mo:02d}', y, mo


@bp.route('/')
@login_required
def home():
    if (g := _guard()):
        return g
    q = request.args.get('q', '').strip().lower()
    mem = S.members()
    hits = [m for m in mem if q and (q in m['member'].lower() or q in f"{m['first']} {m['last']}".lower())] if q else []
    total = sum(m['balance'] for m in mem)
    recent = sorted(S.transactions(), key=lambda t: (t['date'] or date.min), reverse=True)[:15]
    return render_template('wp.html', **_ctx(view='home', q=q, hits=hits[:50], total=total, members_count=len(mem),
                                             check=S.check(), recent=recent))


@bp.route('/member/<member_id>')
@login_required
def member(member_id):
    if (g := _guard()):
        return g
    m = next((m for m in S.members() if m['member'] == member_id), None)
    if not m:
        flash('Member not found.', 'warning')
        return redirect(url_for('wp.home'))
    tx = sorted([t for t in S.transactions() if t['member'] == member_id], key=lambda t: t['date'] or date.min, reverse=True)
    return render_template('wp.html', **_ctx(view='member', m=m, tx=tx))


@bp.route('/event', methods=['GET', 'POST'])
@login_required
def event():
    if (g := _guard()):
        return g
    events = sorted(load_events(), key=lambda e: e.get('date', ''), reverse=True)
    mem = S.members()
    if request.method == 'POST':
        S.refresh()
        mem = S.members()
        code = request.form['event_code']
        ev = _events_by_code().get(code)
        who = _who()
        when = date.fromisoformat(request.form.get('date') or (ev or {}).get('date') or date.today().isoformat())
        balances = {m['member']: m['balance'] for m in mem}
        names = {m['member']: f"{m['first']} {m['last']}" for m in mem}
        rows, errors = [], []
        for mid, pts, duty in zip(request.form.getlist('member'), request.form.getlist('points'), request.form.getlist('duty')):
            mid = mid.split(' ')[0].strip()
            if not mid:
                continue
            try:
                p = float(pts)
            except ValueError:
                errors.append(f'{mid}: points must be a number'); continue
            if (err := L.validate_entry(mid, p, 'Earned', balances)):
                errors.append(err); continue
            rows.append({'date': when, 'member': mid, 'name': names[mid], 'event_code': code,
                         'description': f"{(ev or {}).get('event_name', code)}: {duty or 'Worker'}", 'points': p,
                         'type': 'Earned', 'source': f'Entered in app by {who}'})
        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('wp.html', **_ctx(view='event', events=events, members=mem, form=request.form))
        if rows:
            S.append_transactions(rows)
            log_audit_event('WP_EVENT_POSTED', who, {'event_code': code, 'workers': len(rows), 'points': sum(r['points'] for r in rows)})
            flash(f"Posted {sum(r['points'] for r in rows):g} points for {len(rows)} workers at {code}.", 'success')
        return redirect(url_for('wp.home'))
    return render_template('wp.html', **_ctx(view='event', events=events, members=mem, form={}))


@bp.route('/redeem')
@login_required
def redeem():
    """Redemption requests: pending ones first, then the recent decisions."""
    if (g := _guard()):
        return g
    reqs = sorted(S.requests(), key=lambda r: r['id'], reverse=True)
    return render_template('wp.html', **_ctx(view='requests', pending=[r for r in reqs if r['status'] == 'Pending'],
                                             done=[r for r in reqs if r['status'] != 'Pending'][:25]))


@bp.route('/redeem/new', methods=['GET', 'POST'])
@login_required
def redeem_new():
    if (g := _guard()):
        return g
    if request.method == 'POST':
        S.refresh()
    mem = S.members()
    if request.method == 'POST':
        f = request.form
        mid = f['member'].split(' ')[0].strip()
        who = _who()
        try:
            pts = abs(float(f['points']))
        except ValueError:
            pts = 0
        m = next((m for m in mem if m['member'] == mid), None)
        errors = []
        if not m:
            errors.append('Pick the member from the list.')
        elif pts <= 0:
            errors.append('Points must be a number above zero.')
        elif (err := L.validate_entry(mid, -pts, 'Used', {x['member']: x['balance'] for x in mem})):
            errors.append(err)
        if not f.get('for', '').strip() or not f.get('payee', '').strip():
            errors.append('Say what the points are for and who should be paid.')
        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('wp.html', **_ctx(view='request_new', members=mem, form=f))
        rid = S.add_request({'member': mid, 'name': f"{m['first']} {m['last']}", 'points': pts, 'amount': round(pts * L.POINT_VALUE, 2),
                             'for': f['for'].strip(), 'payee': f['payee'].strip(), 'payee_address': f.get('payee_address', '').strip(),
                             'requested_by': who, 'requested_email': f.get('requested_email', '').strip(), 'notes': f.get('notes', '').strip()})
        log_audit_event('WP_REDEMPTION_REQUESTED', who, {'id': rid, 'member': mid, 'points': pts})
        flash(f'Request {rid} filed. Nothing comes off until it is approved.', 'success')
        return redirect(url_for('wp.redeem_detail', rid=rid))
    form = {'member': request.args.get('member', '')}
    return render_template('wp.html', **_ctx(view='request_new', members=mem, form=form))


@bp.route('/redeem/<rid>', methods=['GET', 'POST'])
@login_required
def redeem_detail(rid):
    if (g := _guard()):
        return g
    if request.method == 'POST':
        S.refresh()
    r = next((r for r in S.requests() if r['id'] == rid), None)
    if not r:
        flash('Request not found.', 'warning')
        return redirect(url_for('wp.redeem'))
    m = next((m for m in S.members() if m['member'] == r['member']), None)
    if request.method == 'GET':
        return render_template('wp.html', **_ctx(view='request', r=r, m=m, fr_on=F.enabled()))
    if r['status'] != 'Pending':
        flash(f'{rid} was already {r["status"].lower()}.', 'warning')
        return redirect(url_for('wp.redeem_detail', rid=rid))
    who = _who()
    if request.form.get('action') == 'decline':
        note = request.form.get('note', '').strip()
        if not note:
            flash('Say why it is declined.', 'danger')
            return redirect(url_for('wp.redeem_detail', rid=rid))
        S.decide_request(rid, 'Declined', decided=date.today().isoformat(), decided_by=who, decision_note=note)
        log_audit_event('WP_REDEMPTION_DECLINED', who, {'id': rid, 'note': note})
        flash(f'{rid} declined. No points were taken.', 'info')
        return redirect(url_for('wp.redeem'))
    if (err := L.validate_entry(r['member'], -r['points'], 'Used', {m['member']: m['balance']} if m else {})):
        flash(err, 'danger')
        return redirect(url_for('wp.redeem_detail', rid=rid))
    # Points first: a funds request without the deduction would pay out points the member still holds.
    S.append_transactions([{'date': date.today(), 'member': r['member'], 'name': r['name'], 'event_code': 'USED',
                            'description': f"{r['for']} ({rid})", 'points': -r['points'], 'type': 'Used',
                            'source': f'Request {rid}, approved in app by {who}'}])
    fr_id, note = '', ''
    if F.enabled():
        method = F.METHODS.get(request.form.get('method'), F.METHODS['check'])
        req = {'event_code': '', 'event_name': 'Worker points redemption', 'program': '',
               'submitter_name': r['requested_by'], 'submitter_email': r['requested_email'], 'submitter_phone': '', 'submitter_address': '',
               'payee_name': r['payee'], 'payee_address': r['payee_address'], 'payee_email': '', 'payee_phone': '', 'method': method,
               'total': r['amount'], 'advances': 0, 'net': r['amount'],
               'notes': f"Points redemption {rid}: {r['name']} #{r['member']}, {r['points']:g} pts for {r['for']}. Approved by {who}.",
               'entered_by': who}
        lines = [{'date': date.today().isoformat(), 'description': f"Worker points: {r['name']} #{r['member']}, {r['for']}",
                  'category': 'Other', 'amount': r['amount']}]
        try:
            fr_id = F.create(req, lines, [])
            fr_notify(fr_id, req, lines, 0)
        except Exception as e:
            note = 'Funds request could not be created; enter it by hand.'
            flash(f'Points were taken off, but the funds request failed ({e}). Please file it by hand.', 'warning')
    S.decide_request(rid, 'Approved', decided=date.today().isoformat(), decided_by=who, funds_request=fr_id, decision_note=note)
    log_audit_event('WP_REDEEMED', who, {'id': rid, 'member': r['member'], 'points': -r['points'], 'funds_request': fr_id})
    flash(f"Approved {rid}: {r['points']:g} points off {r['name']}" + (f', funds request {fr_id} opened.' if fr_id else '.'), 'success')
    return redirect(url_for('wp.redeem_detail', rid=rid))


@bp.route('/allocations', methods=['GET', 'POST'])
@login_required
def allocations():
    if (g := _guard()):
        return g
    month, y, mo = _month(request.values.get('month'))
    if request.method == 'POST':
        S.refresh()
    code = f'AC-{month}'
    roles = S.allocations()
    already = [t for t in S.transactions() if t['type'] == 'Allocation' and t['event_code'] == code]
    plan = [dict(r, points=L.allocation_points(r, mo)) for r in roles]
    if request.method == 'POST':
        if already:
            flash(f'Allocations for {month} were already posted.', 'warning')
            return redirect(url_for('wp.allocations', month=month))
        who = _who()
        rows = [{'date': date(y, mo, 1), 'member': r['member'], 'name': r['name'], 'event_code': code,
                 'description': f"{date(y, mo, 1):%B} allocation: {r['role']}", 'points': r['points'], 'type': 'Allocation',
                 'source': f'Entered in app by {who}'} for r in plan if r['points'] > 0]
        S.append_transactions(rows)
        log_audit_event('WP_ALLOCATIONS_POSTED', who, {'month': month, 'points': sum(r['points'] for r in rows)})
        flash(f"Posted {month} allocations: {sum(r['points'] for r in rows):g} points.", 'success')
        return redirect(url_for('wp.home'))
    return render_template('wp.html', **_ctx(view='allocations', month=month, plan=plan, already=already))


@bp.route('/forfeits', methods=['GET', 'POST'])
@login_required
def forfeits():
    if (g := _guard()):
        return g
    if request.method == 'POST':
        S.refresh()
    mem = S.members()
    if request.method == 'POST':
        mid = request.form['member']
        who = _who()
        m = next((m for m in mem if m['member'] == mid), None)
        if not m or m['balance'] <= 0:
            flash('Nothing to forfeit for that member.', 'warning')
            return redirect(url_for('wp.forfeits'))
        S.append_transactions([{'date': date.today(), 'member': mid, 'name': f"{m['first']} {m['last']}", 'event_code': 'FORFEIT',
                                'description': request.form.get('reason') or f"Membership expired {m['exp']}", 'points': -m['balance'],
                                'type': 'Forfeit', 'source': f'Entered in app by {who}'}])
        log_audit_event('WP_FORFEIT', who, {'member': mid, 'points': -m['balance']})
        flash(f"Forfeited {m['balance']:g} points for {m['first']} {m['last']}.", 'success')
        return redirect(url_for('wp.forfeits'))
    return render_template('wp.html', **_ctx(view='forfeits', cands=L.forfeit_candidates(mem, date.today())))


@bp.route('/month-end')
@login_required
def month_end():
    if (g := _guard()):
        return g
    month, y, mo = _month(request.args.get('month'))
    summ = L.month_end_summary(S.transactions(), _events_by_code(), y, mo)
    return render_template('wp.html', **_ctx(view='month_end', month=month, s=summ, check=S.check(), programs=load_programs()))


@bp.route('/check', methods=['POST'])
@login_required
def set_check():
    try:
        S.refresh()
        S.set_qbo_balance(float(request.form['qbo'].replace(',', '').replace('$', '')), date.fromisoformat(request.form['as_of']))
        log_audit_event('WP_QBO_BALANCE', _who(), {'qbo': request.form['qbo'], 'as_of': request.form['as_of']})
    except (ValueError, KeyError):
        flash('Enter the QBO balance and a date.', 'danger')
    return redirect(request.referrer or url_for('wp.home'))


@bp.route('/member/new', methods=['GET', 'POST'])
@login_required
def new_member():
    if (g := _guard()):
        return g
    if request.method == 'POST':
        S.refresh()
        mid = request.form['member'].strip()
        if not mid.isdigit():
            flash('The SCCA member number should be digits only.', 'danger')
            return redirect(url_for('wp.new_member'))
        if any(m['member'] == mid for m in S.members()):
            flash(f'Member {mid} is already in the ledger.', 'warning')
            return redirect(url_for('wp.member', member_id=mid))
        S.add_member(mid, request.form['last'].strip().title(), request.form['first'].strip().title(),
                     request.form.get('exp', '').strip(), bool(request.form.get('life')))
        log_audit_event('WP_MEMBER_ADDED', _who(), {'member': mid, 'name': f"{request.form['first']} {request.form['last']}"})
        flash('Member added.', 'success')
        return redirect(url_for('wp.member', member_id=mid))
    return render_template('wp.html', **_ctx(view='new_member'))
