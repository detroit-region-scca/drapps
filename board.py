"""Board: everything the board decides, in one place.

- Waiting for the board: budgets not approved yet (or changed since) and open motions. Anyone signed in (not
  volunteers) can see them and send them to the board for a vote; the RE, ARE or treasurer records the result.
- Motions: decisions that are not a budget (money set aside, a big purchase, a policy). Proposed here, voted at a
  meeting or by email, the result recorded with the count.
- Decisions: every recorded vote by date, budgets and motions together, with that meeting's minutes when they are
  posted. Email votes since the last meeting are listed so the next meeting can note them.
- Minutes: the Minutes app (uploads stay with the Secretary and officers).
"""
import os
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import bud_store as B
import motions_store as M
import plan_store as P
from officer import current as officer_current
from utils import load_programs, local_path, log_audit_event, login_required, send_mail

bp = Blueprint('board', __name__, url_prefix='/board')


def board_email():
    return os.environ.get('BOARD_EMAIL', 'bod@drscca.org')


def _me():
    from auth import user
    return user() or {}


def _guard():
    if _me().get('level') == 'volunteer':
        flash('The Board pages are for region officials.', 'warning')
        return redirect(url_for('home'))
    if not M.enabled():
        flash('The Board pages need the Google Sheets storage (PLAN_SHEET_ID).', 'warning')
        return redirect(url_for('home'))
    return None


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Board", who=session.get('who', ''), officer=officer_current(),
                board_email=board_email(), programs=load_programs(), today=date.today().isoformat(), **kw)


def _years():
    this = date.today().year
    return [str(this + 1), str(this), str(this - 1)]


def _abs(link):
    """A stored link as a full URL for an email (links saved from the app are site paths)."""
    if link.startswith('/'):
        return request.host_url.rstrip('/') + link
    return link


def ask_motions(ms):
    """Email the board one message for these motions; replies go to the whole board."""
    who = session.get('who', '')
    parts = []
    for m in ms:
        bits = [f"- {M.summary(m)}  ({m['id']}, proposed by {m['proposed_by']})"]
        if m['details']:
            bits.append('  ' + m['details'].replace('\n', '\n  '))
        if m['link']:
            bits.append('  ' + _abs(m['link']))
        parts.append('\n'.join(bits))
    n = len(ms)
    body = (f"{who} asks the board to vote on {'this motion' if n == 1 else f'these {n} motions'}:\n\n" + '\n\n'.join(parts) +
            "\n\nPlease reply all with your vote (yes or no) so it stays in one thread. Everything waiting for the board:\n"
            + url_for('board.home', _external=True))
    send_mail(f"Board vote: {ms[0]['title'] if n == 1 else f'{n} motions'}", body, board_email(), reply_to=board_email())
    for m in ms:
        M.update(m['id'], asked=date.today().isoformat())
    log_audit_event('BOARD_ASKED', who, {'motions': [m['id'] for m in ms]})


@bp.route('/')
@login_required
def home():
    """Waiting for the board: budgets and motions."""
    if (g := _guard()):
        return g
    import bud
    year = request.args.get('year') or str(date.today().year)
    rows = bud._pending_rows(year) if B.enabled() else []
    open_m = [m for m in M.motions() if m['status'] == 'open']
    return render_template('board.html', **_ctx(view='waiting', rows=rows, motions=open_m, year=year, years=_years()))


@bp.route('/motions', methods=['GET', 'POST'])
@login_required
def motions():
    if (g := _guard()):
        return g
    if request.method == 'POST':
        f = request.form
        title = ' '.join(f.get('title', '').split())[:200]
        if not title:
            flash('Write the motion in one line, for example "Set aside $80,000 of savings for the two motorhomes".', 'warning')
            return redirect(url_for('board.motions'))
        amount = None
        if f.get('amount', '').strip():
            from utils import to_number
            amount = to_number(f.get('amount', '').replace('$', '').replace(',', ''))
            if amount is None:
                flash('The amount must be a number.', 'warning')
                return redirect(url_for('board.motions'))
        link = f.get('link', '').strip()
        if link and not (link.startswith('https://') or local_path(link)):
            link = ''
        who = session.get('who', '')
        mid = M.propose(title, f.get('details', '').strip()[:4000], amount, link, who)
        log_audit_event('MOTION_PROPOSED', who, {'id': mid, 'title': title})
        flash(f'{mid} saved. Send it to the board from Waiting for the board when it is ready.', 'success')
        return redirect(url_for('board.home'))
    year = request.args.get('year', '')
    ms = M.motions()
    if year:
        ms = [m for m in ms if m['id'].startswith(f'M-{year}-')]
    return render_template('board.html', **_ctx(view='motions', motions=ms, year=year, years=_years(),
                                                  prefill=dict(title=request.args.get('title', ''), amount=request.args.get('amount', ''),
                                                               link=request.args.get('link', ''), details=request.args.get('details', ''))))


@bp.route('/ask', methods=['POST'])
@login_required
def ask():
    """Send the ticked motions to the board (budgets go through Events' approve form)."""
    if (g := _guard()):
        return g
    ids = request.form.getlist('motion')
    ms = [m for m in M.motions() if m['id'] in ids and m['status'] == 'open']
    if not ms:
        flash('Tick at least one open motion.', 'warning')
    else:
        ask_motions(ms)
        flash(f"Sent to the board ({board_email()}): {len(ms)} motion{'s' if len(ms) != 1 else ''}.", 'success')
    return redirect(url_for('board.home'))


@bp.route('/motion/<mid>', methods=['POST'])
@login_required
def motion(mid):
    """Record a motion's result (officers), or withdraw it (whoever proposed it, or an officer)."""
    if (g := _guard()):
        return g
    m = M.get(mid)
    if not m:
        flash('There is no such motion.', 'warning')
        return redirect(url_for('board.home'))
    f = request.form
    who = session.get('who', '')
    off = officer_current()
    if f.get('action') == 'withdraw':
        if not off and who != m['proposed_by']:
            flash('Only whoever proposed it, or an officer, can withdraw a motion.', 'warning')
        else:
            M.update(mid, status='withdrawn', decided=date.today().isoformat(), recorded_by=who)
            log_audit_event('MOTION_WITHDRAWN', who, {'id': mid})
            flash(f'{mid} withdrawn.', 'info')
        return redirect(url_for('board.home'))
    if not off:
        flash('Recording a board decision is for the RE, ARE or treasurer.', 'warning')
        return redirect(url_for('board.home'))
    try:
        decided = date.fromisoformat(f.get('decided', '')).isoformat()
    except ValueError:
        flash('The date must be a date.', 'danger')
        return redirect(url_for('board.home'))
    result = f.get('result')
    if result not in ('passed', 'failed'):
        flash('Choose passed or failed.', 'warning')
        return redirect(url_for('board.home'))
    import bud
    how = M.HOW.get(f.get('how', ''), '')
    vote = bud._how_vote({'vote': f.get('vote', '')})
    M.update(mid, status=result, decided=decided, how=how, vote=vote, recorded_by=off['name'])
    log_audit_event('MOTION_DECIDED', off['name'], {'id': mid, 'result': result, 'decided': decided, 'how': how, 'vote': vote})
    flash(f"{mid} recorded as {result} on {decided}.", 'success')
    return redirect(local_path(f.get('next')) or url_for('board.home'))


def decisions(year):
    """Every recorded board decision in a year, newest date first: [{'date', 'items': [...], 'minutes': file or None}]."""
    items = []
    for m in M.motions():
        if m['status'] in ('passed', 'failed') and m['decided'].startswith(year):
            items.append({'date': m['decided'], 'kind': 'motion', 'what': M.summary(m), 'id': m['id'], 'result': m['status'],
                          'how': m['how'], 'vote': m['vote'], 'link': url_for('board.motions', year=m['id'][2:6]) + '#' + m['id']})
    if B.enabled():
        for code, b in B.budgets().items():
            if str(b.get('approved', '')).startswith(year):
                items.append({'date': b['approved'], 'kind': 'budget', 'what': f"Budget: {b['event_name']}", 'id': code,
                              'result': 'passed', 'how': _how_of(b.get('approved_by', '')), 'vote': _vote_of(b.get('approved_by', '')),
                              'link': url_for('bud.detail', code=code)})
    if P.enabled():
        for y in {year, str(int(year) + 1)}:
            info = (P.plan(y).get('OPS') or {}).get('info') or {}
            if str(info.get('approved', '')).startswith(year):
                items.append({'date': info['approved'], 'kind': 'budget', 'what': f'Operations budget {y}', 'id': f'OPS {y}', 'result': 'passed',
                              'how': _how_of(info.get('approved_by', '')), 'vote': _vote_of(info.get('approved_by', '')),
                              'link': url_for('plan.detail', year=y, program='OPS')})
    mins = _minutes_by_date(year)
    out = {}
    for it in items:
        out.setdefault(it['date'], []).append(it)
    return [{'date': d, 'items': sorted(v, key=lambda x: (x['kind'], x['what'])), 'minutes': mins.get(d)} for d, v in sorted(out.items(), reverse=True)]


def _how_of(approved_by):
    """'email vote' or 'board meeting' from 'Name (email vote, 7-0)'; '' when it was not recorded."""
    inside = approved_by.rsplit('(', 1)[1].rstrip(')') if '(' in approved_by else ''
    return next((h for h in M.HOW.values() if inside.startswith(h)), '')


def _vote_of(approved_by):
    inside = approved_by.rsplit('(', 1)[1].rstrip(')') if '(' in approved_by else ''
    return inside.split(', ', 1)[1] if ', ' in inside else ''


def _minutes_by_date(year):
    try:
        import minutes_logic as ML
        import minutes_store as MS
        if not MS.enabled():
            return {}
        out = {}
        for fl in MS.published(int(year)):
            d, label = ML.parse(fl['name'])
            if d:
                out.setdefault(d.isoformat(), fl)
        return out
    except Exception:
        return {}


@bp.route('/decisions')
@login_required
def decisions_page():
    if (g := _guard()):
        return g
    year = request.args.get('year') or str(date.today().year)
    days = decisions(year)
    # Email votes after the last board meeting: the next meeting notes them in its minutes.
    meetings = [d['date'] for d in days if any(i['how'] == 'board meeting' for i in d['items']) or d['minutes']]
    last = max(meetings) if meetings else ''
    since = [i for d in days if d['date'] > last for i in d['items'] if i['how'] == 'email vote']
    return render_template('board.html', **_ctx(view='decisions', days=days, year=year, years=_years(), since=since, last=last))
