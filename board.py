"""Board: everything the board decides, in one place.

- Waiting for the board: budgets not approved yet (or changed since) and open motions. Every official (not volunteers)
  can see them; roles with "Send items to the board" (Admin > Roles) can ask for a vote, and roles with "Record board
  decisions" (by default the RE, ARE, secretary and treasurer) record the result.
- Motions: decisions that are not a budget (money set aside, a big purchase, a policy). Proposed here, voted at a
  meeting or by email, the result recorded with the count.
- Decisions: every recorded vote by date, budgets and motions together, with that meeting's minutes when they are
  posted. Email votes since the last meeting are listed so the next meeting can note them.
- Email votes (ballots_logic, ballots_store): Request email vote opens a ballot on each ticked item and emails every
  director their own signed link (Yes / No / Abstain), while the request itself still goes to the board list for
  discussion. The Board page shows the roll call: who voted what, and who has not. 7 votes decide it; an officer then
  records the result with one click. A vote taken at a meeting replaces the email vote.
- Minutes: the Minutes app (uploads stay with the Secretary and officers).
"""
import os
from datetime import date

from filelock import FileLock
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

import ballots_logic as BL
import ballots_store as BS
import bud_store as B
import motions_store as M
import officials as O
import plan_store as P
import shortlinks
from officer import current as officer_current
from storage import DATA_DIR
from utils import load_programs, local_path, log_audit_event, login_required, send_mail

bp = Blueprint('board', __name__, url_prefix='/board')
# The app runs several workers: recording a decision, and marking a vote decided, are each checked and written as one
# step, so two clicks (or two last votes) at the same moment are recorded, and emailed, once.
RECORD_LOCK = os.path.join(DATA_DIR, 'board_record.lock')
VOTE_LOCK = os.path.join(DATA_DIR, 'board_vote.lock')


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
        flash('The Board pages aren\'t set up on this server yet. Ask admin@drscca.org.', 'warning')
        return redirect(url_for('home'))
    return None


PAGE_TITLES = {'waiting': 'Waiting for the board', 'decisions': 'Decisions', 'vote': 'Board vote'}


def _ctx(**kw):
    kw.setdefault('page_title', PAGE_TITLES.get(kw.get('view'), ''))      # the browser tab: the page's own name
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


def _motion_lines(ms):
    out = []
    for m in ms:
        bits = [f"- Motion {m['id']}: {M.summary(m)} (proposed by {m['proposed_by']})"]
        if m['details']:
            bits.append('  ' + m['details'].replace('\n', '\n  '))
        if m['link']:
            bits.append('  ' + _abs(m['link']))
        out.append('\n'.join(bits))
    return out


def _plain(item):
    """One item without the list bullet, for an email about a single item: its title, then each detail on its own line."""
    _, title, line = item
    first, *rest = line.removeprefix('- ').split('\n')
    head = [title, first[len(title):].lstrip(', ')] if first.startswith(title + ', ') else [first]
    return '\n'.join([h for h in head if h] + [r.strip() for r in rest])


# A vote link works as long as the vote can be open (the page refuses it once the ballot closes); approval links stay 7 days.
VOTE_DAYS = BL.OPEN_DAYS + 1


def _people():
    """The directors who vote (one entry per person), from the read-only officials list."""
    return BL.roster(O.all())


def _ser():
    return URLSafeTimedSerializer(current_app.secret_key, salt='board-vote')


def _vote_link(bid, p):
    url = url_for('board.vote_link', token=_ser().dumps({'b': bid, 'e': p['email'], 'n': p['name']}), _external=True)
    return shortlinks.make(url, VOTE_DAYS)


def _ballot_views(people=None, me=''):
    """{item key: the ballot as the page shows it} for each item's latest ballot: the ballot row plus 't' (tally and
    roll call), 'state', 'summary' and 'mine' (the signed-in director's own vote, '' if none or not a director)."""
    if not BS.enabled():
        return {}
    people = _people() if people is None else people
    allv = BS.votes()
    out = {}
    for b in sorted(BS.ballots(), key=lambda b: b['id']):
        if b['status'] == 'cancelled':
            continue
        t = BL.tally([v for v in allv if v['ballot_id'] == b['id']], people)
        mine = next((r['choice'] for r in t['rows'] if r['email'] == (me or '').lower()), None)
        out[b['item']] = dict(b, t=t, state=BL.state(b, t), summary=BL.summary(b, t), mine=mine,
                              is_voter=mine is not None)
    return out


def _decide_if_done(bid):
    """After a vote: if 7 votes now decide it, mark the ballot decided and tell the board. Returns the result or None.
    Only the vote that marks it decided sends the email (two last votes can arrive at the same moment)."""
    with FileLock(VOTE_LOCK, timeout=60):
        b = BS.get(bid)
        if not b or b['status'] != 'open':
            return None
        t = BL.tally(BS.votes(bid), _people())
        res = BL.outcome(t)
        if not res:
            return None
        BS.update(bid, status=res, result=res, count=BL.count_text(t), decided=date.today().isoformat())
    log_audit_event('BALLOT_DECIDED', 'email vote', {'ballot': bid, 'item': b['item'], 'result': res, 'count': BL.count_text(t)})
    send_mail(f"Board vote {res}: {b['title']}",
              f"{b['title']}: {res} {BL.count_text(t)} by email vote.\n\nThe RE, ARE, secretary or treasurer records it on the Board page:\n"
              + url_for('board.home', _external=True), board_email(), reply_to=board_email())
    return res


def _snap(item):
    """A fingerprint of a budget's numbers as they are now, kept on its ballot when an email vote opens: if it differs
    when the result is recorded, the budget was changed during the vote. '' for a motion or a budget that is gone."""
    if not item.startswith('budget:') or item.count(':') < 2:
        return ''
    _, y, code = item.split(':', 2)
    if code == 'OPS':
        p = P.plan(y).get('OPS') if P.enabled() else None
        return P.lines_hash(P.saved_lines(p['lines'])) if p else ''
    # The same fingerprint "changed since approved" uses: only the budget numbers count, so entering results or
    # re-ordering lines during the vote does not void it.
    b = B.get(code) if B.enabled() else None
    return B.lines_hash(b['lines']) if b else ''


def _still_waiting(bcodes):
    """Of these budgets ({year: [codes]}), the ones still waiting for the board (not approved or put back in draft
    since the page was loaded)."""
    import bud
    out = {}
    for y, codes in bcodes.items():
        waiting = {r['b']['event_code'] for r in bud._pending_rows(y)}
        keep = [c for c in codes if c in waiting]
        if keep:
            out[y] = keep
    return out


def _record(bcodes, ms, decided, result, how, vote, off):
    """Record the board's decision on budgets ({year: [codes]}) and motions. how is 'meeting' or 'email'.
    Returns the phrases for the message. Any open email vote on these items is closed: the recorded vote counts."""
    import bud
    how_text = ', '.join(x for x in (M.HOW.get(how, ''), vote) if x)      # 'email vote, 7-1' for approved_by
    said = []
    if bcodes and result == 'passed':
        n = sum(bud.record_budgets(y, codes, decided, how_text) for y, codes in bcodes.items())
        said.append(f"{n} budget{'s' if n != 1 else ''} approved")
    elif bcodes:
        sum(bud.record_not_approved(codes, decided, how_text) for codes in bcodes.values())
        said.append('budgets left waiting (a budget is only recorded when the board approves it; ask the director to revise it)')
    for m in ms:
        M.update(m['id'], status=result, decided=decided, how=M.HOW.get(how, ''), vote=vote, recorded_by=off['name'])
        log_audit_event('MOTION_DECIDED', off['name'], {'id': m['id'], 'result': result, 'decided': decided, 'how': how_text})
    if ms:
        said.append(f"{len(ms)} motion{'s' if len(ms) != 1 else ''} {result}")
    if result == 'passed':
        import fr
        import fr_store as F
        rids = [m['link'].rstrip('/').rsplit('/', 1)[-1] for m in ms if '/funds/' in (m.get('link') or '')]
        codes = {c for cs in bcodes.values() for c in cs}
        rids += [x['id'] for x in F.requests() if x['status'] == 'Submitted' and (x['event_code'] in codes or (
            not x['event_code'] and bud.program_code(x['program'], x['submitted'][:4]) in codes))]
        if rids:
            fr.announce_ready(rids)
    if BS.enabled():
        keys = {f'budget:{y}:{c}' for y, cs in bcodes.items() for c in cs} | {f"motion:{m['id']}" for m in ms}
        for b in BS.ballots():
            if b['item'] in keys and b['status'] in ('open', 'passed', 'failed', 'expired', ''):
                BS.update(b['id'], status='recorded' if how == 'email' else 'cancelled', recorded_by=off['name'])
    return said


def _picked(f):
    """Ticked items: ({year: [budget codes]}, [motion ids]). A budget's value is budget:<year>:<code>."""
    budgets = {}
    for v in f.getlist('item'):
        if v.startswith('budget:') and v.count(':') >= 2:
            _, y, code = v.split(':', 2)
            budgets.setdefault(y, []).append(code)
    motions = [v.split(':', 1)[1] for v in f.getlist('item') if v.startswith('motion:')]
    return budgets, motions


@bp.route('/', methods=['GET', 'POST'])
@login_required
def home():
    """Everything waiting for the board, budgets and motions in one list: tick items to send them to the board for a
    vote, or (RE, ARE, treasurer) to record the result. Proposing a motion is at the bottom."""
    if (g := _guard()):
        return g
    import bud
    if request.method == 'POST':
        f = request.form
        bcodes, mids = _picked(f)
        ms = [m for m in M.motions() if m['id'] in mids and m['status'] == 'open']
        if not bcodes and not ms:
            flash('Tick at least one item.', 'warning')
            return redirect(url_for('board.home'))
        if f.get('action') == 'ask':
            if 'board_ask' not in set((_me().get('perms') or ())):
                flash("Your role doesn't include sending items to the board.", 'warning')
                return redirect(url_for('board.home'))
            items = []      # (item key, title, email line)
            for y, codes in sorted(bcodes.items()):
                ln, rw = bud.budget_vote_lines(y, codes) if B.enabled() else ([], [])
                items += [(f"budget:{y}:{r['b']['event_code']}", f"Budget: {r['b']['event_name']}", line) for r, line in zip(rw, ln)]
            items += [(f"motion:{m['id']}", f"Motion {m['id']}: {M.summary(m)}", line) for m, line in zip(ms, _motion_lines(ms))]
            n = len(items)
            if not n:
                flash('Nothing to send: those items are already decided.', 'warning')
                return redirect(url_for('board.home'))
            who = session.get('who', '')
            today = date.today().isoformat()
            first = items[0][1].split(': ', 1)[-1] if n == 1 else f'{n} items'
            urgent = f.get('urgent') == 'yes'
            people = _people() if BS.enabled() else []
            bids = [BS.open_ballot(key, title, who, today, snap=_snap(key)) for key, title, _ in items] if people else []
            board_url = url_for('board.home', _external=True)
            u = 'URGENT ' if urgent else ''
            listed = '\n\n'.join(i[2] for i in items)
            if bids:
                subject = f"{u}Discussion: {first}"
                send_mail(subject,
                          f"This email is for discussion. Reply all to discuss.\n\n"
                          f"{who} asks the board to vote on:\n\n{_plain(items[0]) if n == 1 else listed}\n\n"
                          f"Your vote link comes in a separate email.\nCloses {BL.nice_date(BL.closes(today))}.\n\n"
                          f"Who has voted:\n{board_url}",
                          board_email(), reply_to=board_email())
            else:
                send_mail(f"{u}Board vote: {first}",
                          (("URGENT: please vote as soon as you can.\n\n" if urgent else '') +
                           f"{who} asks the board to vote on {'this' if n == 1 else f'these {n} items'}:\n\n{listed}"
                           "\n\nPlease reply all with your vote (yes or no on each) so it stays in one thread. "
                           "Everything waiting for the board:\n" + board_url),
                          board_email(), reply_to=board_email())
            for p in people:
                mine = '\n\n'.join(f"{title}:\n\n{_vote_link(bid, p)}" for (_, title, _), bid in zip(items, bids))
                send_mail(f"{u}Your vote link: {first}",
                          f"This email has your vote link.\n\n{mine}\n\n"
                          f"Closes {BL.nice_date(BL.closes(today))}. To discuss, see the \"{subject}\" email.",
                          p['email'])
            for m in ms:
                M.update(m['id'], asked=today)
            log_audit_event('BOARD_ASKED', who, {'items': [i[0] for i in items], 'ballots': bids, 'urgent': urgent})
            flash(f"{'Urgent vote request' if urgent else 'Vote request'} sent to the board ({board_email()}): {n} item{'s' if n != 1 else ''}"
                  + (f", with a personal voting link to each of {len(people)} directors." if bids else '.'), 'success')
            return redirect(url_for('board.home'))
        if f.get('action') == 'no_vote':
            return _no_vote(bcodes)
        off = officer_current()
        if not off:
            flash('Recording a board decision is for the RE, ARE, secretary or treasurer.', 'warning')
            return redirect(url_for('board.home'))
        try:
            decided = date.fromisoformat(f.get('decided', '')).isoformat()
        except ValueError:
            flash('Enter the date the board decided it.', 'danger')
            return redirect(url_for('board.home'))
        result = f.get('result')
        if result not in ('passed', 'failed'):
            flash('Choose passed or failed.', 'warning')
            return redirect(url_for('board.home'))
        with FileLock(RECORD_LOCK, timeout=60):      # check again and record as one step: a double click records once
            bcodes = _still_waiting(bcodes) if B.enabled() else {}
            ms = [m for m in M.motions() if m['id'] in mids and m['status'] == 'open']
            if not bcodes and not ms:
                flash('Those items are already recorded. Nothing was changed.', 'warning')
                return redirect(url_for('board.home'))
            said = _record(bcodes, ms, decided, result, f.get('how', ''), bud._how_vote({'vote': f.get('vote', '')}), off)
        flash(f"Recorded for {BL.nice_date(decided)}: " + '; '.join(said) + '.', 'success')
        return redirect(url_for('board.home'))
    rows = [dict(r, year=y) for y in bud.pending_years() for r in bud._pending_rows(y)] if B.enabled() else []
    rows.sort(key=lambda r: str(r['b'].get('date', '')))
    open_m = [m for m in M.motions() if m['status'] == 'open']
    views = _ballot_views(me=session.get('email', ''))
    for r in rows:
        r['ballot'] = views.get(f"budget:{r['year']}:{r['b']['event_code']}")
    for m in open_m:
        m['ballot'] = views.get(f"motion:{m['id']}")
    return render_template('board.html', **_ctx(view='waiting', rows=rows, motions=open_m, needed=BL.needed(), BL=BL,
                                                  may_no_vote='no_vote' in set((_me().get('perms') or ())),
                                                  prefill=dict(title=request.args.get('title', ''), amount=request.args.get('amount', ''),
                                                               link=request.args.get('link', ''), details=request.args.get('details', ''))))


def _no_vote(bcodes):
    """Take the ticked budgets off the board's list with no vote (the no_vote permission). The Operations budget always
    needs the board, so it is left alone, as are motions (they have Withdraw)."""
    if 'no_vote' not in set((_me().get('perms') or ())):
        flash("Your role can't take a budget off the board's list.", 'warning')
        return redirect(url_for('board.home'))
    why = ' '.join((request.form.get('why') or '').split())[:200]
    if not why:
        flash('Say why the board need not vote on them.', 'warning')
        return redirect(url_for('board.home'))
    import bud
    who = session.get('who', '')
    note = f"{date.today().isoformat()} by {who}: {why}"
    done = []
    with FileLock(RECORD_LOCK, timeout=60):
        for y, codes in sorted(_still_waiting(bcodes).items()):
            for code in codes:
                b = B.get(code)
                if code == 'OPS' or not b:
                    continue
                B.set_no_vote(code, note)
                bud._cancel_ballots(f"budget:{y}:{code}", who, why=f'{who} marked it as needing no board vote ({why})')
                log_audit_event('BUDGET_NO_VOTE', who, {'event_code': code, 'why': note})
                done.append(b.get('event_name') or code)
                bud.tell_director('no_vote', [b], who=who, why=why)
    if done:
        flash("No board vote needed, off the list: " + '; '.join(done) + '.', 'success')
    else:
        flash('Tick at least one budget. The Operations budget and motions are not taken off this way.', 'warning')
    return redirect(url_for('board.home'))


@bp.route('/motions', methods=['GET', 'POST'])
@login_required
def motions():
    """Propose a motion (the form is at the bottom of Waiting for the board)."""
    if (g := _guard()):
        return g
    if request.method == 'GET':
        return redirect(url_for('board.home') + '#propose')
    f = request.form
    title = ' '.join(f.get('title', '').split())[:200]
    if not title:
        flash('Write the motion in one line.', 'warning')
        return redirect(url_for('board.home') + '#propose')
    amount = None
    link = f.get('link', '').strip()
    if link and not (link.startswith('https://') or local_path(link)):
        link = ''
    who = session.get('who', '')
    mid = M.propose(title, f.get('details', '').strip()[:4000], amount, link, who)
    log_audit_event('MOTION_PROPOSED', who, {'id': mid, 'title': title})
    flash(f'{mid} added to the list. Tick it and press Request email vote when it is ready.', 'success')
    return redirect(url_for('board.home'))


@bp.route('/motion/<mid>/withdraw', methods=['POST'])
@login_required
def withdraw(mid):
    """Withdraw an open motion (whoever proposed it, or an officer). Motions are never deleted."""
    if (g := _guard()):
        return g
    m = M.get(mid)
    who = session.get('who', '')
    if not m or m['status'] != 'open':
        flash('That motion is not open.', 'warning')
    elif not officer_current() and who != m['proposed_by']:
        flash('Only whoever proposed it, or an officer, can withdraw a motion.', 'warning')
    else:
        M.update(mid, status='withdrawn', decided=date.today().isoformat(), recorded_by=who)
        b = BS.current(f'motion:{mid}') if BS.enabled() else None
        if b and b['status'] in ('open', 'passed', 'failed', 'expired', ''):
            BS.update(b['id'], status='cancelled', recorded_by=who)
        log_audit_event('MOTION_WITHDRAWN', who, {'id': mid})
        flash(f'{mid} withdrawn.', 'info')
    return redirect(url_for('board.home'))


def _item_parts(item):
    """('budget', {year: [code]}, []) or ('motion', {}, [motion]) for a ballot's item key."""
    if item.startswith('budget:'):
        _, y, code = item.split(':', 2)
        return 'budget', {y: [code]}, []
    m = M.get(item.split(':', 1)[1])
    return 'motion', {}, [m] if m else []


def _item_link(item):
    kind, bc, ms = _item_parts(item)
    if kind == 'budget':
        code = next(iter(bc.values()))[0]
        year = next(iter(bc))
        return url_for('plan.detail', year=year, program='OPS') if code == 'OPS' else url_for('bud.detail', code=code)
    return url_for('board.home') + (f"#{ms[0]['id']}" if ms else '')


@bp.route('/vote/<token>', methods=['GET', 'POST'])
def vote_link(token):
    """A director's emailed voting link: the signed token says who they are, for this one ballot."""
    if not BS.enabled():
        return redirect(url_for('home'))
    try:
        t = _ser().loads(token, max_age=VOTE_DAYS * 86400)
    except BadSignature:
        return render_template('board.html', **_ctx(view='vote', b=None, msg='This voting link has expired or is not valid.'))
    b = BS.get(t['b'])
    p = next((p for p in _people() if p['email'] == t['e']), None)
    if not b or not p:
        return render_template('board.html', **_ctx(view='vote', b=None, msg='This vote is no longer open, or you are no longer on the board list.'))
    return _vote_page(b, p, via='email link', back=request.url)


@bp.route('/ballot/<bid>/vote', methods=['POST'])
@login_required
def vote_here(bid):
    """Vote from the Board page, signed in as yourself."""
    if (g := _guard()):
        return g
    b = BS.get(bid)
    p = next((p for p in _people() if p['email'] == (session.get('email') or '').lower()), None)
    if not b or not p:
        flash('Only board members vote, each from their own sign-in or emailed link.', 'warning')
        return redirect(url_for('board.home'))
    return _vote_page(b, p, via='board page', back=url_for('board.home'))


def _vote_page(b, p, via, back):
    t = BL.tally(BS.votes(b['id']), _people())
    st = BL.state(b, t)
    msg = ''
    if request.method == 'POST':
        choice = request.form.get('choice', '')
        if st != 'open':
            msg = 'This vote is already closed: ' + BL.summary(b, t)
        elif choice not in BL.CHOICES:
            msg = 'Choose Yes, No or Abstain.'
        else:
            BS.cast(b['id'], p['email'], p['name'], choice, via)
            log_audit_event('BALLOT_VOTE', p['name'], {'ballot': b['id'], 'item': b['item'], 'choice': choice, 'via': via})
            res = _decide_if_done(b['id'])
            msg = f"Thank you: your vote ({BL.LABEL[choice]}) is recorded." + (f" That decides it: {res}." if res else '')
            if via == 'board page':
                flash(f"{b['title']}: " + msg, 'success')
                return redirect(back)
            b = BS.get(b['id'])
            t = BL.tally(BS.votes(b['id']), _people())
            st = BL.state(b, t)
    mine = next((r['choice'] for r in t['rows'] if r['email'] == p['email']), '')
    # The page says "Voting as <me.name>". Someone signed in keeps their own permissions under that name, so the menu
    # (Minutes, Audit log) stays as it was; an emailed link with nobody signed in has only the voter.
    return render_template('board.html', **_ctx(view='vote', page_title=b['title'] or 'Board vote', b=b, t=t, state=st, summary=BL.summary(b, t), me=dict(_me(), **p), mine=mine,
                                                  msg=msg, link=_item_link(b['item']), BL=BL))


@bp.route('/ballot/<bid>/record', methods=['POST'])
@login_required
def record_ballot(bid):
    """An officer records an email vote's result (it is decided once 7 votes agree)."""
    if (g := _guard()):
        return g
    off = officer_current()
    if not off or not BS.get(bid):
        flash('Recording a board decision is for the RE, ARE, secretary or treasurer.', 'warning')
        return redirect(url_for('board.home'))
    with FileLock(RECORD_LOCK, timeout=60):      # check again and record as one step: a double click records once
        b = BS.get(bid)
        t = BL.tally(BS.votes(bid), _people())
        st = BL.state(b, t)
        if st in ('recorded', 'cancelled'):
            flash('That vote is already recorded. Nothing was changed.', 'warning')
            return redirect(url_for('board.home'))
        if st not in ('passed', 'failed'):
            flash('That vote is not decided yet.', 'warning')
            return redirect(url_for('board.home'))
        kind, bc, ms = _item_parts(b['item'])
        ms = [m for m in ms if m['status'] == 'open']
        if kind == 'budget':
            bc = _still_waiting(bc) if B.enabled() else {}
        if not bc and not ms:
            flash(f"{b['title']} is no longer waiting for the board (already recorded, withdrawn or back in draft). Nothing was changed.", 'warning')
            return redirect(url_for('board.home'))
        if kind == 'budget' and st == 'passed' and b.get('snap') and _snap(b['item']) != b['snap']:      # older ballots have no snap
            flash(f"{b['title']} was changed after this vote opened, so the vote can't approve it as it is now. "
                  "Request a new email vote on the budget, or take it to a meeting.", 'danger')
            return redirect(url_for('board.home'))
        if b['status'] == 'open':      # decided by the last vote but not marked yet
            BS.update(bid, status=st, result=st, count=BL.count_text(t), decided=date.today().isoformat())
            b = BS.get(bid)
        said = _record(bc, ms, b['decided'] or date.today().isoformat(), st, 'email', b['count'] or BL.count_text(t), off)
    flash(f"Recorded {b['title']}: {st} {b['count']} by email vote ({'; '.join(said)}).", 'success')
    return redirect(url_for('board.home'))


@bp.route('/ballot/<bid>/remind', methods=['POST'])
@login_required
def remind(bid):
    """Email a fresh voting link to each director who has not voted yet."""
    if (g := _guard()):
        return g
    if 'board_ask' not in set((_me().get('perms') or ())) and not officer_current():
        flash("Your role doesn't include sending items to the board.", 'warning')
        return redirect(url_for('board.home'))
    b = BS.get(bid)
    if not b:
        return redirect(url_for('board.home'))
    t = BL.tally(BS.votes(bid), _people())
    if BL.state(b, t) != 'open':
        flash('That vote is closed.', 'info')
        return redirect(url_for('board.home'))
    todo = [r for r in t['rows'] if not r['choice']]
    for p in todo:
        send_mail(f"Reminder, your board vote: {b['title']}",
                  f"Hi {p['name'].split(' (')[0].split()[0]},\n\nThe board is voting on:\n{b['title']}\n{request.host_url.rstrip('/')}{_item_link(b['item'])}"
                  f"\n\nSo far: {BL.summary(b, t)}\n\nYour vote: {_vote_link(bid, p)}\n\nThis link is yours alone, so please don't forward it.",
                  p['email'])
    log_audit_event('BALLOT_REMINDED', session.get('who', ''), {'ballot': bid, 'to': len(todo)})
    flash(f"Reminder sent to {len(todo)} director{'s' if len(todo) != 1 else ''} who {'have' if len(todo) != 1 else 'has'} not voted.", 'success')
    return redirect(url_for('board.home'))


def decisions(year):
    """Every recorded board decision in a year, newest date first: [{'date', 'items': [...], 'minutes': file or None}]."""
    items = []
    for m in M.motions():
        if m['status'] in ('passed', 'failed') and m['decided'].startswith(year):
            items.append({'date': m['decided'], 'kind': 'motion', 'what': M.summary(m), 'id': m['id'], 'result': m['status'],
                          'how': m['how'], 'vote': m['vote'], 'link': url_for('board.decisions_page', year=m['decided'][:4]) + '#' + m['id']})
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
    if BS.enabled():
        views = {}
        people = _people()
        allv = BS.votes()
        for b in BS.ballots():
            if b['status'] == 'recorded':
                views[b['item'].split(':')[-1]] = BL.tally([v for v in allv if v['ballot_id'] == b['id']], people)
        for it in items:
            it['rollcall'] = (views.get(it['id']) or {}).get('rows')
    mins = _minutes_by_month(year)
    out = {}
    for it in items:
        out.setdefault(it['date'], []).append(it)
    # Minutes are filed by month ("2026-11 Board Minutes.pdf"): they go with that month's meeting date, which is any
    # date with a decision that was not an email vote.
    return [{'date': d, 'items': sorted(v, key=lambda x: (x['kind'], x['what'])),
             'minutes': mins.get(d[:7]) if any(i['how'] != 'email vote' for i in v) else None} for d, v in sorted(out.items(), reverse=True)]


def _how_of(approved_by):
    """'email vote' or 'board meeting' from 'Name (email vote, 7-0)'; '' when it was not recorded."""
    inside = approved_by.rsplit('(', 1)[1].rstrip(')') if '(' in approved_by else ''
    return next((h for h in M.HOW.values() if inside.startswith(h)), '')


def _vote_of(approved_by):
    inside = approved_by.rsplit('(', 1)[1].rstrip(')') if '(' in approved_by else ''
    return inside.split(', ', 1)[1] if ', ' in inside else ''


def _minutes_by_month(year):
    """{'YYYY-MM': the published minutes file of that month's board meeting} (a special meeting's only when the month
    has no regular minutes)."""
    try:
        import minutes_logic as ML
        import minutes_store as MS
        if not MS.enabled():
            return {}
        out = {}
        for fl in sorted(MS.published(int(year)), key=lambda fl: len(fl['name'])):      # the plain name before "(special meeting)"
            month = ML.month_of(fl['name'])
            if month:
                out.setdefault(month, fl)
        return out
    except Exception:
        return {}


@bp.route('/decisions')
@login_required
def decisions_page():
    if (g := _guard()):
        return g
    year = request.args.get('year') or ''
    if not (year.isascii() and year.isdigit() and len(year) == 4):      # anything else in the address: this year
        year = str(date.today().year)
    days = decisions(year)
    # Email votes after the last board meeting: the next meeting notes them in its minutes.
    meetings = [d['date'] for d in days if any(i['how'] == 'board meeting' for i in d['items']) or d['minutes']]
    last = max(meetings) if meetings else ''
    since = [i for d in days if d['date'] > last for i in d['items'] if i['how'] == 'email vote']
    return render_template('board.html', **_ctx(view='decisions', page_title=f'Decisions {year}', days=days, year=year, years=_years(), since=since, last=last))
