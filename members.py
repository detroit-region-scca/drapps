"""Members: worker points and member milestones (DRSCCA Members sheet + the public drscca.org sheet).

  Members      (keeper/officers) the SCCA list upload, members and points, lapsed, QuickBooks
  Enter points (everyone signed in) typed names or the worker points sheet, checked on a review page before saving
  Milestones   (keeper/officers) each month's new members and anniversaries, published to drscca.org
Nothing is added or spent without a click from the keeper or an officer.
"""
import io
import os
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import members_logic as L
import members_store as S
from utils import local_path, load_events, log_audit_event, login_required

bp = Blueprint('members', __name__, url_prefix='/members')
KEEPER_ROLE = 'Points Keeper'
SCCA_REPORTS = 'https://my.scca.com/eweb/DynamicPage.aspx?webcode=regionreports'


def _me():
    from auth import user
    return user() or {}


def _keeper():
    me = _me()
    return 'points' in (me.get('perms') or ())


def _who():
    return _me().get('name') or session.get('who') or 'Unknown'


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Members", keeper=_keeper(), point_value=L.POINT_VALUE, today_=date.today(),
                pick_label=L.pick_label, ledger_url=S.members_sheet_url(), current_perms=sorted(_me().get('perms') or ()),
                structure=L.POINT_STRUCTURE, **kw)


def _back():
    """Where a form posted from another Members page returns to (only this app's own pages)."""
    b = request.form.get('back', '')
    return b if local_path(b).startswith('/members') else url_for('members.home')


def _month_step(month, n):
    y, m = int(month[:4]), int(month[5:]) + n
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f'{y:04d}-{m:02d}'


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
    return sorted(load_events(), key=lambda e: e.get('date', ''), reverse=True)[:80]


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
    open_value = round(sum(e['points'] for e in L.unreconciled(ent)) * L.POINT_VALUE, 2)
    lists = [m['listed'] for m in mem if m.get('listed')]
    return render_template('members.html', **_ctx(view='home', members=mem, total=total, reserve=reserve, open_value=open_value,
                                                  lapsed=L.lapsed(mem, t),
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
    """Add points in two steps: the names (typed, or the worker points sheet uploaded) go to a review page where each
    line shows the member it is, the members it might be, and any points already saved for the event; nothing is saved
    until Save there. The API posts step=save straight away (its caller is the confirmation); the same checks run."""
    if (g := _guard()):
        return g
    mem = S.members()
    by_id = {m['member']: m for m in mem}
    events = _events_for_me()
    if request.method == 'GET':
        grid = []
        if request.args.get('member') in by_id:
            grid = [{'member': L.pick_label(by_id[request.args['member']])}]
        return render_template('members.html', **_ctx(view='enter', members=mem, events=events, form={}, grid=grid))
    f = request.form
    code = f.get('event', '').strip()
    if code == 'none':
        code = ''
    ev = _events_by_code().get(code) or {}
    if f.get('event', '').strip() not in ('none', '') and not ev:
        flash('Pick the event from the list.', 'danger')
        return render_template('members.html', **_ctx(view='enter', members=mem, events=events, form=f, grid=[]))
    if ev:          # one code per event in the ledger (the one the event list shows); both count for duplicates
        code = ev.get('custom_tag') or ev.get('unique_code') or code
    codes = {c for c in (code, ev.get('custom_tag'), ev.get('unique_code')) if c}
    first = L.to_date(f.get('first')) or L.to_date(ev.get('date')) or date.today()
    what = f"Worked {ev.get('event_name')}" if ev else (f.get('what', '').strip() or 'Volunteer job')
    if f.get('step') == 'save':
        return _save_points(f, mem, by_id, events, code, codes, ev, first, what)
    # step 1: the lines, typed and/or from the uploaded sheet
    lines, sheet = [], None
    for n, p in zip(f.getlist('member'), f.getlist('points')):
        if (n or '').strip():
            lines.append({'name': ' '.join(n.split()), 'points': L.num(p) if str(p or '').strip() else 1.0})
    up = request.files.get('sheet')
    if up and up.filename:
        try:
            sheet = _read_sheet(up)
        except ValueError as e:
            flash(str(e), 'danger')
            grid = [{'member': x['name'], 'points': x['points']} for x in lines]
            return render_template('members.html', **_ctx(view='enter', members=mem, events=events, form=f, grid=grid))
        lines += [{'name': x['name'], 'points': x['points']} for x in sheet['lines']]
    if not lines:
        flash('Add at least one person, or upload the worker points sheet.', 'danger')
        return render_template('members.html', **_ctx(view='enter', members=mem, events=events, form=f, grid=[]))
    rows = L.review_lines(lines, mem, S.entries(), codes)
    return _review(f, code, codes, ev, first, what, rows, sheet)


def _read_sheet(up):
    name = up.filename.lower()
    data = up.read()
    if name.endswith('.csv'):
        import csv
        rows = list(csv.reader(io.StringIO(data.decode('utf-8-sig', errors='replace'))))
        return L.read_points_sheet([(up.filename, rows)])
    if not name.endswith(('.xlsx', '.xlsm')):
        raise ValueError('Upload the sheet as .xlsx (or .csv). For an old .xls file, open it and save it as .xlsx.')
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)   # read only: some forms carry drawings openpyxl cannot load
        return L.read_points_sheet([(ws.title, list(ws.iter_rows(values_only=True))) for ws in wb.worksheets])
    except ValueError:
        raise
    except Exception:
        raise ValueError('Could not open that file. Save it as .xlsx and try again.')


def _review(f, code, codes, ev, first, what, rows, sheet=None, chosen=None, seen_before=None):
    """The review page. chosen: {line n: member} when coming back from a refused save, so the picks are kept, except a
    member who got points for the event since the page was made (seen_before): that one goes back to Leave out, so a
    second entry always takes a deliberate pick. Each page has a one-time token: it can be saved once."""
    import json
    import secrets
    have = L.existing_points(S.entries(), codes)
    for r in rows:
        if chosen is not None:
            r['choice'] = chosen.get(r['n'], '')
            k = L.canon(r['choice']) if r['choice'] else ''
            if k and have.get(k, {}).get('count', 0) > (seen_before or {}).get(k, 0):
                r['choice'] = ''
        else:                       # leave out by default only what needs a decision: a duplicate, a repeat, no match
            r['choice'] = '' if (r['already'] or r['twice'] or not r['member']) else r['member']
    ids = {L.canon(r['member']) for r in rows if r['member']} | {L.canon(c['member']) for r in rows for c in r['candidates']}
    seen = json.dumps({k: have[k]['count'] for k in sorted(ids) if k in have})
    tabs = []
    for t in (sheet or {}).get('tabs', []):
        got = sum(x['points'] or 0 for x in t['lines'])
        tabs.append(dict(t, sum=got, ok=t['total'] is None or abs(got - t['total']) < 0.01))
    sheet_name = (request.files.get('sheet').filename if request.files.get('sheet') else '') or f.get('sheet_name', '')
    return render_template('members.html', **_ctx(view='review', seen=seen, token=secrets.token_urlsafe(16), code=code, ev=ev,
                                                  first=first, what=what, rows=rows, tabs=tabs, sheet_name=sheet_name, form=f))


def _save_points(f, mem, by_id, events, code, codes, ev, first, what):
    import json
    names, picks, pts = f.getlist('name'), f.getlist('member'), f.getlist('points')
    from_review = bool(names)
    try:                     # how many entries each member had for this event when the review page was made
        seen = {str(k): int(v) for k, v in json.loads(f.get('seen') or '{}').items()}
    except (ValueError, TypeError, AttributeError):
        seen = {}
    chosen, left_out, bad = [], [], []
    for i, raw in enumerate(picks):
        nm = names[i] if i < len(names) else raw
        if not (raw or '').strip():
            if (nm or '').strip():
                left_out.append(nm.strip())
            continue
        mid = L.pick_id(raw, by_id)
        if not mid:
            bad.append(f"{raw}: pick the name from the list (or ask the points keeper to add them)")
            continue
        p = pts[i] if i < len(pts) else ''
        chosen.append({'n': i, 'member': mid, 'name': nm, 'points': L.num(p) if str(p or '').strip() else (0.0 if from_review else 1.0),
                       'seen': seen.get(L.canon(mid), 0)})
    if not chosen and not bad:
        bad = ['Nobody to save: every line is left out.']
    rows = [{'date': first, 'member': c['member'], 'name': L.full_name(by_id[c['member']]), 'points': c['points'], 'what': what,
             'event': code, 'kind': 'Earned' if code else 'Role points'} for c in chosen]

    def build(entries):      # run again under the lock, on a fresh read of the Points tab
        return rows, bad + L.save_check(by_id, entries, codes, chosen, allow_duplicates=f.get('allow_duplicates') == 'yes')
    n, problems = (0, bad) if bad else S.save_checked(build, _who(), f.get('token', ''))
    if problems == ['ALREADY_SAVED']:
        flash('This list was already saved. Nothing was added again.', 'warning')
        return redirect(url_for('members.home') if _keeper() else url_for('members.enter'))
    if problems:
        for p in problems:
            flash(p, 'danger')
        if not from_review:
            grid = [{'member': m, 'points': p} for m, p in zip(picks, pts)]
            return render_template('members.html', **_ctx(view='enter', members=mem, events=events, form=f, grid=grid))
        lines = [{'name': nm, 'points': L.num(pts[i]) if i < len(pts) and str(pts[i]).strip() else None} for i, nm in enumerate(names)]
        rows2 = L.review_lines(lines, mem, S.entries(), codes)
        return _review(f, code, codes, ev, first, what, rows2, chosen={i: (picks[i] if i < len(picks) else '') for i in range(len(names))},
                       seen_before=seen)
    total = sum(e['points'] for e in rows)
    log_audit_event('POINTS_ENTERED', _who(), {'event': code, 'rows': n, 'points': total, 'status': L.APPROVED,
                                               'left_out': len(left_out), 'sheet': f.get('sheet_name', '')})
    if not _keeper():
        from utils import send_mail
        send_mail(f"Worker points added: {ev.get('event_name') or 'entries'} from {_who()}",
                  f"{_who()} saved {n} worker points entries{(' for ' + ev.get('event_name', '')) if ev else ''}"
                  + (f" from the sheet {f.get('sheet_name')}" if f.get('sheet_name') else '') + ":\n\n"
                  + '\n'.join(f"  {e['name']}: {e['points']:g}" for e in rows)
                  + (("\n\nLeft out (not matched to a member, or already entered):\n" + '\n'.join(f'  {x}' for x in left_out)) if left_out else '')
                  + f"\n\nThey count now. Fix anything on the Members page: {url_for('members.home', _external=True)}",
                  recipient=os.environ.get('MEMBERSHIP_EMAIL', 'membership@drscca.org'))
    msg = f"Saved {n} entries ({total:+g} points)." + (f" {len(left_out)} left out." if left_out else '')
    flash(msg if _keeper() else msg + ' The points keeper has a copy.', 'success')
    return redirect(url_for('members.home') if _keeper() else url_for('members.enter'))


@bp.route('/log')
@login_required
def log():
    """Who added and who spent worker points: every Points entry of a year, newest first. Open to everyone who uses
    worker points (names, points, what for, the event and who entered it; never member numbers)."""
    if (g := _guard()):
        return g
    if not ({'points_enter', 'points'} & set(_me().get('perms') or ())):
        flash('The worker points log is for people who enter or keep worker points.', 'warning')
        return redirect(url_for('home'))
    ent = S.entries()
    years = sorted({e['date'].year for e in ent if e.get('date')} | {date.today().year}, reverse=True)
    try:
        year = int(request.args.get('year') or years[0])
    except ValueError:
        year = years[0]
    if year not in years:          # the picker only offers years with entries
        year = years[0]
    ev = _events_by_code()
    rows = sorted([e for e in ent if e.get('date') and e['date'].year == year],
                  key=lambda e: (e['date'], e.get('row', 0)), reverse=True)
    for e in rows:
        e['event_name'] = (ev.get(e.get('event')) or {}).get('event_name', '')
    added = sum(e['points'] for e in rows if e['points'] > 0 and e.get('status') == L.APPROVED)
    spent = -sum(e['points'] for e in rows if e['points'] < 0 and e.get('status') == L.APPROVED)
    return render_template('members.html', **_ctx(view='log', rows=rows, years=years, year=year, added=added, spent=spent))


@bp.route('/upload', methods=['POST'])
@login_required
def upload():
    """Load the SCCA Member List: refresh names and dates on the Members tab, keep the original file in Drive."""
    if (g := _guard(keeper=True)):
        return g
    up = request.files.get('report')
    if not up or not up.filename:
        flash('Choose the Member List file first.', 'warning')
        return redirect(_back())
    data = up.read()
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        report = L.roster(list(wb.worksheets[0].iter_rows(values_only=True)))
    except ValueError as e:
        flash(str(e), 'warning')
        return redirect(_back())
    except Exception:
        flash('Could not open that file. Download the Member List again from SCCA (an .xlsx file) and retry.', 'warning')
        return redirect(_back())
    saved = S.save_list_file(data, _who())
    before = S.members()
    merged = L.merge_roster(before, report, date.today())
    S.write_members(merged)
    log_audit_event('MEMBER_LIST_LOADED', _who(), {'file': saved['name'], 'on_list': len(report), 'members': len(merged)})
    flash(f'Loaded the SCCA member list: {len(report)} current members ({len(merged) - len(before)} new). The original is kept in Drive.', 'success')
    return redirect(_back())


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
    flash(f"Deleted {-sum(r['points'] for r in rows):g} points from {len(rows)} members who are not current.", 'success')
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
        flash(f'{month}: {len(new)} names saved. Click Review and publish to open the draft.' if new else f'Removed {month}.', 'success')
        return redirect(url_for('members.milestones', month=month))
    norm = lambda g: {k: sorted(x.lower() for x in v) for k, v in g.items() if v}
    lists = [m['listed'] for m in mem if m.get('listed')]
    return render_template('members.html', **_ctx(view='milestones', month=month, proposed=proposed, published=published,
                                                  differs=bool(proposed) and norm(proposed) != norm(published),
                                                  latest_list=max(lists) if lists else None, anniv_years=L.MILESTONE_YEARS,
                                                  month_label=date(int(month[:4]), int(month[5:]), 1).strftime('%B %Y'),
                                                  prev_month=_month_step(month, -1), next_month=_month_step(month, 1),
                                                  public_url=S.public_sheet_url(), list_url=S.list_folder_url(),
                                                  scca_reports=SCCA_REPORTS,
                                                  website_drafts=os.environ.get('WEBSITE_URL', 'https://drscca.org').rstrip('/')
                                                  + f'/wp-admin/admin-post.php?action=drscca_sa_review&month={month}'))


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
        m = by_id.get(L.pick_id(f.get('member'), by_id))
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


def send_books(who='DRSCCA Apps'):
    """Email the treasurer the journal entry for everything not yet reconciled. Used by the button and on the 1st."""
    from utils import send_mail
    open_entries = L.unreconciled(S.entries())
    if not open_entries:
        return 0
    jes, used = L.journal_entries(open_entries, _events_by_code())
    bk = {'jes': jes, 'used': used}
    st = _reserve()
    value = round(sum(m['points'] for m in S.members()) * L.POINT_VALUE, 2)
    first, last = min(e['date'] for e in open_entries), max(e['date'] for e in open_entries)
    subject, body = L.books_email(bk, f"{first:%-m/%-d/%Y} to {last:%-m/%-d/%Y}", st and st['amount'], value)
    send_mail(subject, body, recipient=os.environ.get('NOTIFICATION_EMAIL', 'treasurer@drscca.org'))
    log_audit_event('POINTS_BOOKS_EMAILED', who, {'entries': len(open_entries), 'journal_entries': len(jes)})
    return len(open_entries)


@bp.route('/quickbooks', methods=['GET', 'POST'])
@login_required
def quickbooks():
    """Entries not yet booked in QuickBooks, the journal entry for them, and marking them reconciled."""
    if (g := _guard(keeper=True)):
        return g
    if request.method == 'POST':
        f = request.form
        if f.get('action') == 'email':
            n = send_books(_who())
            flash(f'Emailed the journal entry for {n} entries to the treasurer.' if n else 'Nothing to book.', 'success')
            return redirect(url_for('members.quickbooks'))
        rows = [int(x) for x in f.getlist('row') if x.isdigit()]
        ref = f.get('ref', '').strip()
        if not rows:
            flash('Tick the entries that are booked.', 'warning')
            return redirect(url_for('members.quickbooks'))
        label = f"{L.to_date(f.get('booked')) or date.today()}" + (f' {ref}' if ref else '')
        S.mark_reconciled(rows, label)
        log_audit_event('POINTS_RECONCILED', _who(), {'entries': len(rows), 'ref': label})
        flash(f'Marked {len(rows)} entries reconciled ({label}).', 'success')
        return redirect(url_for('members.quickbooks'))
    ent = S.entries()
    open_entries = sorted(L.unreconciled(ent), key=lambda e: (e['date'] or date.min, e['row']))
    total = round(sum(m['points'] for m in S.members()) * L.POINT_VALUE, 2)
    reserve = _reserve()
    open_value = round(sum(e['points'] for e in open_entries) * L.POINT_VALUE, 2)
    diff = round(total - reserve['amount'], 2) if reserve else None
    jes, used = L.journal_entries(open_entries, _events_by_code())
    return render_template('members.html', **_ctx(view='quickbooks', open_entries=open_entries, jes=jes, used=used,
                                                  total=total, reserve=reserve, diff=diff, open_value=open_value,
                                                  unexplained=round(diff - open_value, 2) if diff is not None else None))


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
            mid = L.pick_id(mid, by_id)
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
