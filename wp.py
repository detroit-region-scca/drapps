"""Worker Points pages. Everything is read from and written to the ledger Google Sheet (wp_store)."""
import os
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

import wp_logic as L
import wp_store as S
from utils import load_events, load_programs, log_audit_event, login_required

bp = Blueprint('wp', __name__, url_prefix='/wp')


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Worker Points", enabled=S.enabled(), point_value=L.POINT_VALUE, **kw)


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
        return redirect(url_for('index'))
    return None


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
        code = request.form['event_code']
        ev = _events_by_code().get(code)
        who = request.form.get('entered_by', '').strip() or 'Unknown'
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


@bp.route('/redeem', methods=['GET', 'POST'])
@login_required
def redeem():
    if (g := _guard()):
        return g
    mem = S.members()
    if request.method == 'POST':
        mid = request.form['member'].split(' ')[0].strip()
        who = request.form.get('entered_by', '').strip() or 'Unknown'
        try:
            pts = -abs(float(request.form['points']))
        except ValueError:
            flash('Points must be a number.', 'danger')
            return redirect(url_for('wp.redeem'))
        bal = {m['member']: m['balance'] for m in mem}
        if (err := L.validate_entry(mid, pts, 'Used', bal)):
            flash(err, 'danger')
            return redirect(url_for('wp.redeem'))
        m = next(m for m in mem if m['member'] == mid)
        S.append_transactions([{'date': date.today(), 'member': mid, 'name': f"{m['first']} {m['last']}", 'event_code': 'USED',
                                'description': request.form.get('note', '').strip() or 'Redeemed', 'points': pts, 'type': 'Used',
                                'source': f'Entered in app by {who}'}])
        log_audit_event('WP_REDEEMED', who, {'member': mid, 'points': pts, 'note': request.form.get('note', '')})
        flash(f"{m['first']} {m['last']} redeemed {abs(pts):g} points (${abs(pts) * L.POINT_VALUE:,.2f}).", 'success')
        return redirect(url_for('wp.member', member_id=mid))
    return render_template('wp.html', **_ctx(view='redeem', members=mem))


@bp.route('/allocations', methods=['GET', 'POST'])
@login_required
def allocations():
    if (g := _guard()):
        return g
    month = request.values.get('month') or date.today().strftime('%Y-%m')
    y, mo = map(int, month.split('-'))
    code = f'AC-{month}'
    roles = S.allocations()
    already = [t for t in S.transactions() if t['type'] == 'Allocation' and t['event_code'] == code]
    plan = [dict(r, points=L.allocation_points(r, mo)) for r in roles]
    if request.method == 'POST':
        if already:
            flash(f'Allocations for {month} were already posted.', 'warning')
            return redirect(url_for('wp.allocations', month=month))
        who = request.form.get('entered_by', '').strip() or 'Unknown'
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
    mem = S.members()
    if request.method == 'POST':
        mid = request.form['member']
        who = request.form.get('entered_by', '').strip() or 'Unknown'
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
    month = request.args.get('month') or date.today().strftime('%Y-%m')
    y, mo = map(int, month.split('-'))
    summ = L.month_end_summary(S.transactions(), _events_by_code(), y, mo)
    return render_template('wp.html', **_ctx(view='month_end', month=month, s=summ, check=S.check(), programs=load_programs()))


@bp.route('/check', methods=['POST'])
@login_required
def set_check():
    try:
        S.set_qbo_balance(float(request.form['qbo'].replace(',', '').replace('$', '')), date.fromisoformat(request.form['as_of']))
        log_audit_event('WP_QBO_BALANCE', request.form.get('entered_by', 'Unknown'), {'qbo': request.form['qbo'], 'as_of': request.form['as_of']})
    except (ValueError, KeyError):
        flash('Enter the QBO balance and a date.', 'danger')
    return redirect(request.referrer or url_for('wp.home'))


@bp.route('/member/new', methods=['GET', 'POST'])
@login_required
def new_member():
    if (g := _guard()):
        return g
    if request.method == 'POST':
        mid = request.form['member'].strip()
        if any(m['member'] == mid for m in S.members()):
            flash(f'Member {mid} is already in the ledger.', 'warning')
            return redirect(url_for('wp.member', member_id=mid))
        S.add_member(mid, request.form['last'].strip().title(), request.form['first'].strip().title(),
                     request.form.get('exp', '').strip(), bool(request.form.get('life')))
        log_audit_event('WP_MEMBER_ADDED', request.form.get('entered_by', 'Unknown'), {'member': mid, 'name': f"{request.form['first']} {request.form['last']}"})
        flash('Member added.', 'success')
        return redirect(url_for('wp.member', member_id=mid))
    return render_template('wp.html', **_ctx(view='new_member'))
