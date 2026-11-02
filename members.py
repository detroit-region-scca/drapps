"""Members: worker points and member milestones (DRSCCA Members sheet + the public drscca.org sheet).

  Members      (keeper/officers) the SCCA list upload, worker lists to approve, members and points, lapsed, QuickBooks
  Enter points (everyone signed in) one grid for an event or a single member; event chairs' entries wait for the keeper
  Milestones   (keeper/officers) each month's new members and anniversaries, published to drscca.org
Nothing is added or spent without a click from the keeper or an officer.
"""
import io
import os
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import members_logic as L
import members_store as S
from utils import load_events, log_audit_event, login_required

bp = Blueprint('members', __name__, url_prefix='/members')
KEEPER_ROLE = 'Points Keeper'
SCCA_REPORTS = 'https://my.scca.com/eweb/DynamicPage.aspx?webcode=regionreports'


def _me():
    from auth import user
    return user() or {}


def _keeper():
    me = _me()
    return me.get('level') == 'officer' or KEEPER_ROLE in (me.get('roles') or [])


def _who():
    return _me().get('name') or session.get('who') or 'Unknown'


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Members", keeper=_keeper(), point_value=L.POINT_VALUE, today_=date.today(),
                structure=L.POINT_STRUCTURE, specialties=L.SPECIALTIES, volunteer_job=L.VOLUNTEER_JOB, **kw)


def _guard(keeper=False):
    if not S.enabled():
        flash('Members needs MEMBERS_SHEET_ID and the Google Sheets storage.', 'warning')
        return redirect(url_for('home'))
    if keeper and not _keeper():
        return redirect(url_for('members.enter'))
    return None


def _events_by_code():
    out = {}
    for e in load_events():
        for c in (e.get('unique_code'), e.get('custom_tag')):
            if c:
                out[c] = e
    return out


def _events_for_me():
    evs = sorted(load_events(), key=lambda e: e.get('date', ''), reverse=True)
    me = _me()
    if me.get('chair_only'):
        mine = set(me.get('chair_events', []))
        return [e for e in evs if e.get('unique_code') in mine or e.get('custom_tag') in mine]
    return evs[:80]


def _reserve():
    try:
        import plan_store
        st = plan_store.status().get('Worker points reserve')
        return st and {'amount': st['amount'], 'as_of': st['as_of']}
    except Exception:
        return None


@bp.route('/')
@login_required
def home():
    if (g := _guard(keeper=True)):
        return g
    mem = sorted(S.members(), key=lambda m: (m['last'].lower(), m['first'].lower()))
    ent = S.entries()
    total = sum(m['points'] for m in mem)
    reserve = _reserve()
    t = date.today()
    months = [(t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12), (t.year, t.month)]
    evs = _events_by_code()
    books = [dict(L.month_books(ent, evs, y, mo), label=date(y, mo, 1).strftime('%B %Y'), ym=f'{y:04d}-{mo:02d}') for y, mo in months]
    lists = [m['listed'] for m in mem if m.get('listed')]
    return render_template('members.html', **_ctx(view='home', members=mem, total=total, reserve=reserve, books=books,
                                                  pending=L.pending_groups(ent), lapsed=L.lapsed(mem, t),
                                                  latest_list=max(lists) if lists else None, list_url=S.list_folder_url(),
                                                  sheet_url=S.members_sheet_url(), scca_reports=SCCA_REPORTS))


@bp.route('/member/<mid>')
@login_required
def member(mid):
    if (g := _guard(keeper=True)):
        return g
    m = next((m for m in S.members() if m['member'] == mid), None)
    if not m:
        flash('Member not found.', 'warning')
        return redirect(url_for('members.home'))
    hist = sorted([e for e in S.entries() if e['member'] == mid], key=lambda e: (e['date'] or date.min, e['row']), reverse=True)
    return render_template('members.html', **_ctx(view='member', m=m, hist=hist))


@bp.route('/enter', methods=['GET', 'POST'])
@login_required
def enter():
    if (g := _guard()):
        return g
    mem = S.members()
    by_id = {m['member']: m for m in mem}
    events = _events_for_me()
    if request.method == 'POST':
        f = request.form
        code = f.get('event', '').strip()
        if _me().get('chair_only') and code not in {e.get('unique_code') for e in events} | {e.get('custom_tag') for e in events}:
            code = ''
        ev_date = L.to_date((_events_by_code().get(code) or {}).get('date'))
        first = L.to_date(f.get('first')) or ev_date or date.today()
        last = L.to_date(f.get('last')) or first
        days = max(1, (last - first).days + 1)
        specialty = f.get('specialty_other', '').strip() if f.get('specialty') == 'Other' and f.get('specialty_other', '').strip() else f.get('specialty', '')
        grid = [{'member': n, 'points': p} for n, p in zip(f.getlist('member'), f.getlist('points'))]
        rows, problems = L.rows_from_form(f.getlist('member'), f.getlist('points'), by_id, code, first, specialty, default_points=days)
        keeper = _keeper()
        if keeper:
            problems += [f"{e['name']}: not a member yet; add them on the Members page first" for e in rows if not e['member']]
            problems += L.check_spending(rows, {m['member']: m['points'] for m in mem})
        if problems or not rows:
            for p in problems or ['Add at least one person.']:
                flash(p, 'danger')
            return render_template('members.html', **_ctx(view='enter', members=mem, events=events, form=f, grid=grid))
        status = L.APPROVED if keeper else L.PENDING
        n = S.add_entries(rows, status, _who())
        log_audit_event('POINTS_ENTERED', _who(), {'event': code, 'rows': n, 'points': sum(e['points'] for e in rows), 'status': status})
        if keeper:
            flash(f"Saved {n} entries ({sum(e['points'] for e in rows):+g} points).", 'success')
            return redirect(url_for('members.home'))
        from utils import send_mail
        ev = _events_by_code().get(code) or {}
        send_mail(f"Worker points to approve: {ev.get('event_name') or 'entries'} from {_who()}",
                  f"{_who()} sent {n} worker points entries{(' for ' + ev.get('event_name', '')) if ev else ''}:\n\n"
                  + '\n'.join(f"  {e['name']}: {e['points']:g} ({e['what']})" for e in rows)
                  + f"\n\nApprove them: {url_for('members.home', _external=True)}",
                  recipient=os.environ.get('MEMBERSHIP_EMAIL', 'membership@drscca.org'))
        flash(f'Thanks. {n} entries went to the points keeper, who will approve them.', 'success')
        return redirect(url_for('members.enter'))
    grid = []
    if request.args.get('member') in by_id:
        m = by_id[request.args['member']]
        grid = [{'member': f"{m['member']} {L.full_name(m)}"}]
    return render_template('members.html', **_ctx(view='enter', members=mem, events=events, form={}, grid=grid))


@bp.route('/approve', methods=['POST'])
@login_required
def approve():
    if (g := _guard(keeper=True)):
        return g
    f = request.form
    rows = {e['row']: e for e in S.entries() if e['status'] == L.PENDING}
    ids = [int(x) for x in f.getlist('row') if x.isdigit() and int(x) in rows]
    if f.get('action') == 'reject':
        S.delete_rows(ids)
        log_audit_event('POINTS_REJECTED', _who(), {'rows': len(ids)})
        flash(f'Removed {len(ids)} entries without adding points.', 'info')
        return redirect(url_for('members.home'))
    by_id = {m['member']: m for m in S.members()}
    todo, problems = [], []
    for n in ids:
        e = rows[n]
        mid = (f.get(f'member_{n}') or e['member']).split(' ')[0].strip()
        if mid not in by_id:
            problems.append(f"{e['name']}: pick the member (or add them first)"); continue
        todo.append(dict(e, member=mid, name=L.full_name(by_id[mid]), points=L.num(f.get(f'points_{n}') or e['points'])))
    if problems:
        for p in problems:
            flash(p, 'danger')
        return redirect(url_for('members.home'))
    S.set_status(todo, L.APPROVED, _who())
    log_audit_event('POINTS_APPROVED', _who(), {'rows': len(todo), 'points': sum(e['points'] for e in todo)})
    flash(f"Approved {len(todo)} entries ({sum(e['points'] for e in todo):+g} points).", 'success')
    return redirect(url_for('members.home'))


@bp.route('/upload', methods=['POST'])
@login_required
def upload():
    """Load the SCCA Member List: refresh names and dates on the Members tab, keep the original file in Drive."""
    if (g := _guard(keeper=True)):
        return g
    up = request.files.get('report')
    if not up or not up.filename:
        flash('Choose the Member List file first.', 'warning')
        return redirect(url_for('members.home'))
    data = up.read()
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        report = L.roster(list(wb.worksheets[0].iter_rows(values_only=True)))
    except ValueError as e:
        flash(str(e), 'warning')
        return redirect(url_for('members.home'))
    except Exception:
        flash('Could not open that file. Download the Member List again from SCCA (an .xlsx file) and retry.', 'warning')
        return redirect(url_for('members.home'))
    saved = S.save_list_file(data, _who())
    before = S.members()
    merged = L.merge_roster(before, report, date.today())
    S.write_members(merged)
    log_audit_event('MEMBER_LIST_LOADED', _who(), {'file': saved['name'], 'on_list': len(report), 'members': len(merged)})
    flash(f'Loaded the SCCA member list: {len(report)} current members ({len(merged) - len(before)} new). The original is kept in Drive.', 'success')
    return redirect(url_for('members.home'))


@bp.route('/add-member', methods=['POST'])
@login_required
def add_member():
    if (g := _guard(keeper=True)):
        return g
    f = request.form
    mid = f.get('member', '').strip()
    if not mid or not f.get('last', '').strip():
        flash('Enter the SCCA member number and the name.', 'danger')
        return redirect(url_for('members.home'))
    if any(m['member'] == mid for m in S.members()):
        return redirect(url_for('members.member', mid=mid))
    S.add_member(mid, f.get('first', '').strip(), f['last'].strip(), bool(f.get('life')))
    log_audit_event('MEMBER_ADDED', _who(), {'member': mid})
    flash('Member added.', 'success')
    return redirect(url_for('members.member', mid=mid))


@bp.route('/forfeit', methods=['POST'])
@login_required
def forfeit():
    if (g := _guard(keeper=True)):
        return g
    mem = S.members()
    cands = L.lapsed(mem, date.today())
    if not cands:
        flash('Nobody to forfeit.', 'info')
        return redirect(url_for('members.home'))
    rows = [{'date': date.today(), 'member': m['member'], 'name': L.full_name(m), 'points': -m['points'], 'event': '', 'kind': 'Forfeit',
             'what': f"Forfeit: membership lapsed ({'expired ' + m['expires'].isoformat() if m.get('expires') else 'not on the SCCA member list'})"}
            for m in cands]
    S.add_entries(rows, L.APPROVED, _who())
    log_audit_event('POINTS_FORFEITED', _who(), {'members': len(rows), 'points': sum(r['points'] for r in rows)})
    flash(f"Forfeited {-sum(r['points'] for r in rows):g} points from {len(rows)} lapsed members.", 'success')
    return redirect(url_for('members.home'))


# ---------- Milestones ----------

@bp.route('/milestones', methods=['GET', 'POST'])
@login_required
def milestones():
    if (g := _guard(keeper=True)):
        return g
    month = (request.values.get('month') or '').strip()
    if not (len(month) == 7 and month[4] == '-' and month[:4].isdigit() and month[5:].isdigit()):
        month = f'{date.today():%Y-%m}'
    mem = S.members()
    proposed = L.milestones(mem, month)
    published = {}
    for r in S.milestone_rows():
        r = list(r) + [''] * 4
        if str(r[0]).strip() == month and str(r[2]).strip().isdigit():
            published.setdefault(int(r[2]), []).append(str(r[1]).strip())
    if request.method == 'POST':
        if request.form.get('action') == 'publish':
            new = [[month, n, y, ''] for y, names in proposed.items() for n in names]
        else:
            new = []
            for y in L.MILESTONE_YEARS:
                for line in request.form.get(f'y{y}', '').splitlines():
                    if line.strip():
                        new.append([month, ' '.join(line.split()), y, ''])
        S.save_milestones(month, new)
        log_audit_event('MILESTONES_SAVED', _who(), {'month': month, 'names': len(new)})
        flash(f'{month}: {len(new)} names on drscca.org (shows within the hour).' if new else f'Removed {month} from drscca.org.', 'success')
        return redirect(url_for('members.milestones', month=month))
    norm = lambda g: {k: sorted(x.lower() for x in v) for k, v in g.items() if v}
    lists = [m['listed'] for m in mem if m.get('listed')]
    return render_template('members.html', **_ctx(view='milestones', month=month, proposed=proposed, published=published,
                                                  differs=bool(proposed) and norm(proposed) != norm(published),
                                                  latest_list=max(lists) if lists else None, years=L.MILESTONE_YEARS,
                                                  public_url=S.public_sheet_url()))


@bp.route('/use', methods=['GET', 'POST'])
@login_required
def use():
    """Spend a member's points (renewal, entry, banquet, merchandise). Keeper and officers only."""
    if (g := _guard(keeper=True)):
        return g
    mem = S.members()
    by_id = {m['member']: m for m in mem}
    if request.method == 'POST':
        f = request.form
        m = by_id.get((f.get('member') or '').split(' ')[0])
        what = f.get('for', '')
        if what == 'Other' or f.get('note', '').strip():
            what = f"{what}: {f.get('note', '').strip()}" if what != 'Other' else (f.get('note', '').strip() or 'Other')
        entry, problem = (None, 'Pick the member from the list.') if not m else L.use_entry(m, f.get('points'), what, L.to_date(f.get('date')) or date.today())
        if problem:
            flash(problem, 'danger')
            return render_template('members.html', **_ctx(view='use', members=mem, form=f, uses=L.USE_FOR))
        S.add_entries([entry], L.APPROVED, _who())
        log_audit_event('POINTS_USED', _who(), {'member': entry['member'], 'points': entry['points'], 'for': entry['what']})
        flash(f"Used {-entry['points']:g} points from {entry['name']} for {entry['what']}. If the region pays for it, file a funds request.", 'success')
        return redirect(url_for('members.member', mid=entry['member']))
    form = {}
    if request.args.get('member') in by_id:
        m = by_id[request.args['member']]
        form = {'member': f"{m['member']} {L.full_name(m)}"}
    return render_template('members.html', **_ctx(view='use', members=mem, form=form, uses=L.USE_FOR))


def send_books(ym, who='DRSCCA Apps'):
    """Email the treasurer the worker points journal entries for 'YYYY-MM'. Used by the button and the nightly job."""
    from utils import send_mail
    y, mo = int(ym[:4]), int(ym[5:])
    books = L.month_books(S.entries(), _events_by_code(), y, mo)
    st = _reserve()
    value = round(sum(m['points'] for m in S.members()) * L.POINT_VALUE, 2)
    subject, body = L.books_email(books, date(y, mo, 1).strftime('%B %Y'), st and st['amount'], value)
    send_mail(subject, body, recipient=os.environ.get('NOTIFICATION_EMAIL', 'treasurer@drscca.org'))
    log_audit_event('POINTS_BOOKS_EMAILED', who, {'month': ym, 'lines': len(books['lines'])})


@bp.route('/books/email', methods=['POST'])
@login_required
def books_email():
    if (g := _guard(keeper=True)):
        return g
    ym = request.form.get('month', '')
    if not (len(ym) == 7 and ym[4] == '-'):
        return redirect(url_for('members.home'))
    send_books(ym, _who())
    flash(f'Emailed the {ym} journal entries to the treasurer.', 'success')
    return redirect(url_for('members.home'))


@bp.route('/auto', methods=['GET', 'POST'])
@login_required
def auto():
    """People who get points every month. On the 1st their points are added automatically (the user's choice)."""
    if (g := _guard(keeper=True)):
        return g
    mem = S.members()
    by_id = {m['member']: m for m in mem}
    if request.method == 'POST':
        f = request.form
        if f.get('action') == 'prepare':
            t = date.today()
            n = S.prepare_auto(t.year, t.month, _who())
            flash(f'Added {n} role points entries for {t:%B}.' if n else f'{t:%B} is already added.', 'info')
            return redirect(url_for('members.home'))
        rows, problems = [], []
        for mid, role, season, off in zip(f.getlist('member'), f.getlist('role'), f.getlist('season'), f.getlist('offseason')):
            mid = mid.split(' ')[0].strip()
            if not mid and not role.strip():
                continue
            if mid not in by_id:
                problems.append(f'{mid or role}: pick the member from the list'); continue
            rows.append({'member': mid, 'name': L.full_name(by_id[mid]), 'role': role.strip() or 'Volunteer job',
                         'season': L.num(season), 'offseason': L.num(off)})
        if problems:
            for p in problems:
                flash(p, 'danger')
        else:
            S.save_auto_rows(rows)
            log_audit_event('AUTO_ADD_SAVED', _who(), {'people': len(rows)})
            flash('Saved.', 'success')
        return redirect(url_for('members.auto'))
    return render_template('members.html', **_ctx(view='auto', members=mem, auto=S.auto_rows()))
