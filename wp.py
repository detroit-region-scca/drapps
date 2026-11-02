"""Worker Points. Everything is read from and written to the ledger Google Sheet (wp_store).

Kept deliberately small:
  Points (home)      members, balances, waiting worker lists from event chairs, lapsed members to forfeit
  Member             history and one "add or use points" form
  Submit workers     event chairs send who worked (volunteers see only this)
  Review             the points keeper checks a submitted list and approves it
Role allocations, the month-end QuickBooks summary and the public standings run in the nightly job (tools/qbo_nightly.py).
"""
import os
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import wp_logic as L
import wp_store as S
from utils import load_events, log_audit_event, login_required

bp = Blueprint('wp', __name__, url_prefix='/wp')

SPECIALTIES = ['Whole event (event chair)', 'Registration', 'Tech / scrutineering', 'Timing and scoring',
               'Flagging and communications', 'Grid / pit out', 'Course / rally route', 'Safety / emergency', 'Special event', 'Other']


def _me():
    from auth import user
    return user() or {}


KEEPER_ROLES = {'Points Keeper', 'Treasurer'}


def _is_keeper():
    """Only the Worker Points Keeper and the Treasurer (officials list roles) manage points; everyone else submits workers."""
    return bool(KEEPER_ROLES & set(_me().get('roles') or []))


def _keeper_only():
    if not _is_keeper():
        return redirect(url_for('wp.report'))
    return None


def _ctx(**kw):
    kw.setdefault('me_level', _me().get('level', ''))
    kw.setdefault('keeper', _is_keeper())
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Worker Points", enabled=S.enabled(), point_value=L.POINT_VALUE, **kw)


def _who():
    return _me().get('name') or session.get('who') or 'Unknown'


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


def _roster():
    """The latest SCCA member list (uploaded in Member Milestones), or None. It decides who is a current member."""
    try:
        import sa_store
        return sa_store.latest_roster() if sa_store.roster_enabled() else None
    except Exception:
        return None


def _recent_events():
    return sorted(load_events(), key=lambda e: e.get('date', ''), reverse=True)[:80]


@bp.route('/')
@login_required
def home():
    if (g := _guard()):
        return g
    if (k := _keeper_only()):     # everyone but the keeper: straight to sending who worked
        return k
    mem = sorted(S.members(), key=lambda m: (m['last'].lower(), m['first'].lower()))
    try:
        waiting = [r for r in S.reports() if r['status'] == 'Submitted']
    except Exception:
        waiting = []
    roster = _roster()
    lapsed = L.lapsed_from_roster(mem, roster['expiry'], date.today()) if roster else []
    t = date.today()
    months = [(t.year, t.month), (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)]
    evs = _events_by_code()
    books = [dict(L.month_end_summary(S.transactions(), evs, y, mo), label=date(y, mo, 1).strftime('%B %Y')) for y, mo in reversed(months)]
    return render_template('wp.html', **_ctx(view='home', members=mem, total=sum(m['balance'] for m in mem), check=S.check(),
                                             waiting=waiting, lapsed=lapsed, roster=roster, books=books))


@bp.route('/member/<member_id>', methods=['GET', 'POST'])
@login_required
def member(member_id):
    if (g := _guard()):
        return g
    if (k := _keeper_only()):
        return k
    if request.method == 'POST':
        S.refresh()
    m = next((m for m in S.members() if m['member'] == member_id), None)
    if not m:
        flash('Member not found.', 'warning')
        return redirect(url_for('wp.home'))
    if request.method == 'POST':
        f = request.form
        try:
            pts = float(f.get('points', ''))
        except ValueError:
            pts = 0
        reason = f.get('reason', '').strip()
        code = f.get('event_code', '').strip()
        typ = 'Used' if pts < 0 else ('Earned' if code else 'Adjustment')
        err = L.validate_entry(member_id, pts, typ, {member_id: m['balance']})
        if not err and not reason:
            err = 'Say what it is for.'
        if err:
            flash(err, 'danger')
            return redirect(url_for('wp.member', member_id=member_id))
        S.append_transactions([{'date': date.fromisoformat(f.get('date') or date.today().isoformat()), 'member': member_id,
                                'name': f"{m['first']} {m['last']}", 'event_code': code or typ.upper(), 'description': reason,
                                'points': pts, 'type': typ, 'source': f'Entered in app by {_who()}'}])
        log_audit_event('WP_ENTRY', _who(), {'member': member_id, 'points': pts, 'type': typ, 'reason': reason})
        flash(f"{'Added' if pts > 0 else 'Used'} {abs(pts):g} points for {m['first']} {m['last']}.", 'success')
        return redirect(url_for('wp.member', member_id=member_id))
    tx = sorted([t for t in S.transactions() if t['member'] == member_id], key=lambda t: t['date'] or date.min, reverse=True)
    roster = _roster()
    rexp = None
    if roster:
        mid = str(m['member']).strip()
        rexp = roster['expiry'].get(mid) or roster['expiry'].get(mid.split('-')[0]) or 'missing'
    return render_template('wp.html', **_ctx(view='member', m=m, tx=tx, events=_recent_events(), roster=roster, roster_exp=rexp))


@bp.route('/member/add', methods=['POST'])
@login_required
def add_member():
    if (g := _guard()):
        return g
    if (k := _keeper_only()):
        return k
    S.refresh()
    f = request.form
    mid = f.get('member', '').strip()
    if not mid.isdigit() or not f.get('last', '').strip():
        flash('Enter the SCCA member number (digits) and the name.', 'danger')
        return redirect(url_for('wp.home'))
    if any(m['member'] == mid for m in S.members()):
        return redirect(url_for('wp.member', member_id=mid))
    S.add_member(mid, f['last'].strip(), f.get('first', '').strip(), bool(f.get('life')))
    log_audit_event('WP_MEMBER_ADDED', _who(), {'member': mid, 'name': f"{f.get('first', '')} {f['last']}"})
    flash('Member added.', 'success')
    return redirect(url_for('wp.member', member_id=mid))


@bp.route('/forfeit-lapsed', methods=['POST'])
@login_required
def forfeit_lapsed():
    """Forfeit the points of every member whose membership has lapsed (life members never lapse)."""
    if (g := _guard()):
        return g
    if (k := _keeper_only()):
        return k
    S.refresh()
    roster = _roster()
    if not roster:
        flash('Upload the SCCA member list in Member Milestones first; it decides who has lapsed.', 'warning')
        return redirect(url_for('wp.home'))
    cands = L.lapsed_from_roster(S.members(), roster['expiry'], date.today())
    if not cands:
        flash('Nobody to forfeit.', 'info')
        return redirect(url_for('wp.home'))
    S.append_transactions([{'date': date.today(), 'member': m['member'], 'name': f"{m['first']} {m['last']}", 'event_code': 'FORFEIT',
                            'description': f"Membership lapsed ({'expired ' + m['roster_exp'].isoformat() if m['roster_exp'] else 'not on the SCCA member list'} as of {roster['createdTime'][:10]})",
                            'points': -m['balance'], 'type': 'Forfeit',
                            'source': f'Entered in app by {_who()}'} for m in cands])
    log_audit_event('WP_FORFEIT', _who(), {'members': len(cands), 'points': -sum(m['balance'] for m in cands)})
    flash(f"Forfeited {sum(m['balance'] for m in cands):g} points from {len(cands)} lapsed members.", 'success')
    return redirect(url_for('wp.home'))


@bp.route('/submit', methods=['GET', 'POST'])
@bp.route('/report', methods=['GET', 'POST'])
@login_required
def report():
    """Event chairs and specialty chiefs send who worked. The points keeper reviews it before any points post."""
    if (g := _guard()):
        return g
    me = _me()
    events = _recent_events()
    if me.get('chair_only'):   # signed in only as an event chair: just their own events
        mine = set(me.get('chair_events', []))
        events = [e for e in load_events() if (e.get('unique_code') in mine or e.get('custom_tag') in mine)]
    if request.method == 'POST':
        f = request.form
        code = f.get('event_code', '')
        ev = _events_by_code().get(code)
        if me.get('chair_only') and code not in set(me.get('chair_events', [])):
            ev = None
        workers = f.get('workers', '').strip()
        if not ev or not workers:
            flash('Pick the event and list at least one worker.', 'warning')
            return render_template('wp.html', **_ctx(view='submit', events=events, specialties=SPECIALTIES, form=f))
        rid = S.add_report({'event_code': code, 'event_name': ev.get('event_name', ''), 'date': f.get('date') or ev.get('date', ''),
                            'specialty': f.get('specialty', ''), 'workers': workers, 'notes': f.get('notes', '').strip(),
                            'submitted_by': me.get('name') or _who(), 'email': me.get('email', '')})
        log_audit_event('WP_REPORT_SUBMITTED', _who(), {'id': rid, 'event_code': code})
        from utils import send_mail
        send_mail(f'Event workers {rid}: {ev.get("event_name", code)}',
                  f"{me.get('name') or 'Someone'} sent the workers for {ev.get('event_name', code)} ({code}), {f.get('specialty', '')}.\n\n"
                  f"{workers}\n\n{f.get('notes', '').strip()}\n\nCheck and approve: {url_for('wp.review', rid=rid, _external=True)}",
                  recipient=os.environ.get('MEMBERSHIP_EMAIL', 'membership@drscca.org'))
        flash(f'Thanks. {rid} went to the points keeper, who will record the points.', 'success')
        return redirect(url_for('wp.report'))
    return render_template('wp.html', **_ctx(view='submit', events=events, specialties=SPECIALTIES, form={}))


@bp.route('/review/<rid>', methods=['GET', 'POST'])
@login_required
def review(rid):
    """Check a submitted list (names matched to members, points from role and days) and approve it."""
    if (g := _guard()):
        return g
    if (k := _keeper_only()):
        return k
    rep = next((r for r in S.reports() if r['id'] == rid), None)
    if not rep:
        flash(f'{rid} not found.', 'warning')
        return redirect(url_for('wp.home'))
    if request.method == 'POST':
        if rep['status'] != 'Submitted':
            flash(f"{rid} was already {rep['status'].lower()}.", 'info')
            return redirect(url_for('wp.home'))
        if request.form.get('action') == 'close':
            S.close_report(rid, 'Closed', _who())
            flash(f'{rid} closed without adding points.', 'info')
            return redirect(url_for('wp.home'))
        S.refresh()
        mem = {m['member']: m for m in S.members()}
        when = date.fromisoformat(rep['date'] or date.today().isoformat())
        rows, problems = [], []
        for mid, pts, duty in zip(request.form.getlist('member'), request.form.getlist('points'), request.form.getlist('duty')):
            mid = mid.split(' ')[0].strip()
            if not mid:
                continue
            if mid not in mem:
                problems.append(f'{mid} is not in the ledger'); continue
            try:
                p = float(pts)
            except ValueError:
                problems.append(f'{mid}: points must be a number'); continue
            if p <= 0:
                continue
            rows.append({'date': when, 'member': mid, 'name': f"{mem[mid]['first']} {mem[mid]['last']}", 'event_code': rep['event_code'],
                         'description': f"{rep['event_name']}: {duty or 'Worker'}", 'points': p, 'type': 'Earned',
                         'source': f'{rid}, approved in app by {_who()}'})
        if problems or not rows:
            for p in problems or ['Nothing to add: every row needs a member and points.']:
                flash(p, 'danger')
            return redirect(url_for('wp.review', rid=rid))
        S.append_transactions(rows)
        S.close_report(rid, 'Posted', _who())
        log_audit_event('WP_EVENT_POSTED', _who(), {'id': rid, 'event_code': rep['event_code'], 'workers': len(rows),
                                                    'points': sum(r['points'] for r in rows)})
        flash(f"Added {sum(r['points'] for r in rows):g} points for {len(rows)} workers.", 'success')
        return redirect(url_for('wp.home'))
    mem = S.members()
    rows = L.parse_report(rep['workers'], mem)
    if rep['specialty'] and not rep['specialty'].startswith('Whole'):
        for r in rows:
            r['duty'] = f"{rep['specialty']}: {r['duty']}"
    return render_template('wp.html', **_ctx(view='review', rep=rep, rows=rows, members=mem))
