"""Events: every event of the year in one list, from the annual plan line through registration, budget and board
approval to QuickBooks actuals. The event page holds the event's details, its budget (the region budget form, kept
in the Event Budgets sheet, bud_store), funds requests and actuals. Registering a new event is the "Add event" page
(app.index)."""
import os
import re
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import bud_store as B
import fr_store as F
import officials as O
import plan_store as P
from officer import current as officer_current, officer_required
from utils import load_events, load_programs, local_path, log_audit_event, login_required, request_qbo_refresh, to_number

bp = Blueprint('bud', __name__, url_prefix='/events')

SECTIONS = (('Income', 'inc'), ('Expense', 'exp'))


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Events", who=session.get('who', ''), budgets_on=B.enabled(), officer=officer_current(),
                months=['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], **kw)


def _guard():
    if not B.enabled():
        flash('Event budgets need the Google Sheets storage (STORAGE_BACKEND=sheets and BUDGETS_SHEET_ID).', 'warning')
        return redirect(url_for('home'))
    return None


def _code(e):
    return e.get('custom_tag') or e.get('unique_code')


PROGRAM_CODE = re.compile(r'^([A-Z]{2,5})-(\d{4})$')


def program_code(program, year):
    """The budget code for a program's spending that is not one event, like RY-2027. Event codes start with #."""
    return f"{program}-{year}"


def program_of(code):
    """(program, year) when code is a program budget code, else None."""
    m = PROGRAM_CODE.match(code or '')
    return (m.group(1), m.group(2)) if m and m.group(1) in load_programs() else None


def _event(code):
    """The registered event, or for a program budget code a stand-in event (no id, so it cannot be edited or deleted)."""
    if (pg := program_of(code)):
        program, year = pg
        return {'event_name': f"{load_programs()[program]} program budget {year}", 'program_code': program,
                'date': f"{year}-01-01", 'program_budget': True}
    return next((e for e in load_events() if _code(e) == code), None)


def program_actuals(program, year):
    """QuickBooks money on a program's accounts for a year that is not in any of its event projects, by account,
    shaped like an event's qbo_actuals entry, or None before the first sync."""
    if not (P.enabled() and B.enabled()):
        return None
    acts, as_of = P.actuals(year)
    pa = acts.get(program)
    if not pa:
        return None
    left = {}
    for ln in pa['lines']:
        k = (ln['section'], ln['account'])
        left[k] = left.get(k, 0.0) + ln['amount']
    codes = {_code(e) for e in load_events() if e.get('program_code') == program and e.get('date', '').startswith(str(year))}
    for c, q in B.qbo_actuals().items():
        if c in codes:
            for ln in q['lines']:
                k = (ln['section'], ln['account'])
                if k in left:
                    left[k] -= ln['amount']
    lines = [{'section': s, 'account': a, 'amount': round(v, 2)} for (s, a), v in left.items() if abs(v) >= 0.5]
    inc = sum(ln['amount'] for ln in lines if ln['section'] == 'Income')
    exp = sum(ln['amount'] for ln in lines if ln['section'] == 'Expense')
    return {'project': 'Program accounts, not in an event project', 'as_of': as_of, 'lines': lines,
            'totals': {'income': round(inc, 2), 'expense': round(exp, 2), 'surplus': round(inc - exp, 2)}}


def _money(v):
    v = str(v or '').replace(',', '').replace('$', '').strip()
    if not v:
        return None
    try:
        return round(to_number(v), 2)
    except ValueError:
        return 'bad'


def _plan_line(code):
    """The annual plan line this event is tied to, or None."""
    return P.line_for(code) if P.enabled() else None


def _status(b):
    """'draft' (pulled back, not on the board's list), 'approved', 'changed' (edited after approval), 'budget' or None."""
    if not b:
        return None
    if b.get('draft'):
        return 'draft'
    if b.get('approved'):
        return 'changed' if b.get('updated', '') > b['approved'] else 'approved'
    return 'budget'


def _month_label(d):
    """'2026-01-09' -> 'Jan 2026'."""
    try:
        return date.fromisoformat(str(d)[:10]).strftime('%b %Y')
    except ValueError:
        return str(d)[:4]


def _warnings(e, b, today=None):
    """What still needs doing for a registered event, as [(reason, action)]: action 'budget' or 'results'.
    Shown as a warning sign on the events list and spelled out on the event page."""
    if not B.enabled() or not e or e.get('program_budget'):
        return []
    try:
        past = date.fromisoformat(str(e.get('date', ''))[:10]) < (today or date.today())
    except ValueError:
        return []
    if not b:
        return [('No budget' if past else 'No budget yet', 'budget')]
    if past and not any(ln.get('actual') is not None for ln in b.get('lines', [])):
        return [('The event is over: enter its reported results', 'results')]
    return []


def _last_year_events(code, e, keep=''):
    """This program's events from the year before this one, by date, plus `keep` (an earlier link) if it is older.
    Each: code, label, has_budget."""
    if program_of(code):
        return []
    try:
        last = str(int(str(e.get('date', ''))[:4]) - 1)
    except ValueError:
        return []
    prog, buds = e.get('program_code', ''), B.budgets()
    evs = [x for x in load_events() if x.get('program_code') == prog and (x.get('custom_tag') or x.get('unique_code')) != code
           and (str(x.get('date', '')).startswith(last) or (keep and (x.get('custom_tag') or x.get('unique_code')) == keep))]
    evs.sort(key=lambda x: str(x.get('date', '')))
    return [{'code': c, 'label': f"{x.get('event_name', '')} · {_month_label(x.get('date', ''))}", 'has_budget': c in buds}
            for x in evs for c in [x.get('custom_tag') or x.get('unique_code')]]


def _year_over_year(code):
    """Rows for the event page when "Last year's event" is set: each year's budgeted net and QuickBooks result."""
    b = B.get(code)
    if not b or not b.get('last_year_event'):
        return []
    q, ev = B.qbo_actuals(), {(x.get('custom_tag') or x.get('unique_code')): x for x in load_events()}
    rows = []
    for h in B.history(code):
        bb, x = h['b'] or {}, ev.get(h['code']) or {}
        qt = (q.get(h['code']) or {}).get('totals')
        rows.append({'code': h['code'], 'date': str(x.get('date') or bb.get('date') or ''), 'name': x.get('event_name') or bb.get('event_name', h['code']),
                     'budget': ((bb.get('totals') or {}).get('budget') or {}).get('surplus'), 'qbo': qt})
    return rows


def _received(code):
    """Money reported received at this event (cash and checks), newest first."""
    import received_store as R
    if not R.enabled() or program_of(code):
        return []
    return sorted((r for r in R.reports() if r['event_code'] == code), key=lambda r: r['id'], reverse=True)


def _funds(code):
    if not F.enabled():
        return []
    if (pg := program_of(code)):
        return [r for r in F.requests() if not r['event_code'] and r['program'] == pg[0] and r['submitted'][:4] == pg[1]
                and r['status'] != 'Rejected']
    return [r for r in F.requests() if r['event_code'] == code and r['status'] != 'Rejected']


def plan_name(item):
    """A plan line's item without the old "2027-01-RR " numbering, as an event name."""
    return re.sub(r'^\d{4}-\d{2}-\w+\s+', '', item or '')


def register_url(year, program, ln):
    """The Add event page filled in from a plan line, and tied to it once saved."""
    return url_for('index', program=program, event_name=plan_name(ln['item']), plan_line=f"{ln['sort']:g}",
                   month=f"{year}-{ln['month']:02d}" if ln['month'] else '')


@bp.route('/')
@login_required
def home():
    """The season schedule: registered events and the plan's events still to register, by month.
    No money here; plan, budget and actual per event live on the Annual Plan's program pages."""
    events = load_events()
    plan_years = P.years() if P.enabled() else []
    years = sorted({e.get('date', '')[:4] for e in events if e.get('date', '')[:4].isdigit()} | set(plan_years) | {str(date.today().year)},
                   reverse=True)
    year = request.args.get('year') or str(date.today().year)
    only = request.args.get('program', '')
    programs = load_programs()
    names = dict(programs)
    buds = B.budgets() if B.enabled() else {}
    qbo = B.qbo_actuals() if B.enabled() else {}
    today = date.today()
    rows = []
    for e in events:
        d = e.get('date', '')
        if not d.startswith(year) or (only and e.get('program_code') != only) or e.get('program_budget'):
            continue
        try:
            days = (date.fromisoformat(d) - today).days
        except ValueError:
            days = None
        rows.append({'e': e, 'code': _code(e), 'program': e.get('program_code', ''), 'date': d, 'name': e.get('event_name', ''),
                     'days': days, 'past': days is not None and days < 0,
                     'warnings': _warnings(e, buds.get(_code(e)), today),
                     # The QuickBooks project's own name once it exists; else the name to give it (as on the event page).
                     'qbo_name': (qbo.get(_code(e)) or {}).get('project') or f"{d[:4]} {e.get('program_code', '')} {e.get('event_name', '')} {_code(e)}"})
    # Plan lines with a month and no registered event yet: events the director expects but has not registered.
    # Lines with no month (supplies, trophies) are not events; past months are left to the Annual Plan.
    codes = {r['code'] for r in rows}
    first = 1 if int(year) > today.year else 13 if int(year) < today.year else today.month
    for pc, p in (P.plan(year) if P.enabled() else {}).items():
        if pc == 'OPS' or (only and pc != only):
            continue
        for ln in p['lines']:
            if ln['month'] and ln['month'] >= first and ln['event_code'] not in codes and not P.placeholder(ln['item']):
                rows.append({'e': None, 'code': '', 'program': pc, 'date': f"{year}-{ln['month']:02d}", 'name': plan_name(ln['item']),
                             'days': None, 'past': False, 'warnings': [], 'register': register_url(year, pc, ln)})
    rows.sort(key=lambda r: (r['date'], r['name']))
    months = []
    for r in rows:
        m = r['date'][:7]
        if not months or months[-1]['key'] != m:
            months.append({'key': m, 'label': date(int(m[:4]), int(m[5:7]), 1).strftime('%B'), 'rows': []})
        months[-1]['rows'].append(r)
    for m in months:
        m['past'] = all(r['past'] for r in m['rows'])
    ahead = [r for r in rows if r['e'] and not r['past']]
    summary = {'next': ahead[0] if ahead else None, 'upcoming': len(ahead), 'done': sum(1 for r in rows if r['past']),
               'planned': sum(1 for r in rows if not r['e'])}
    return render_template('bud.html', **_ctx(view='list', season=months, year=year, years=years, s=summary,
                                              programs=programs, only=only, names=names))


@bp.route('/event/<path:code>')
@login_required
def detail(code):
    e = _event(code)
    b = B.get(code) if B.enabled() else None
    if not e and not b:
        flash('Event not found.', 'warning')
        return redirect(url_for('bud.home'))
    pg = program_of(code)
    q = program_actuals(*pg) if pg else (B.qbo_actuals().get(code) if B.enabled() else None)
    plan = None if pg else _plan_line(code)
    return render_template('bud.html', **_ctx(view='detail', code=code, e=e or {}, b=b, funds=_funds(code), received=_received(code), yoy=_year_over_year(code),
                                              warnings=_warnings(e, b),
                                              q=q, plan=plan,
                                              status=_status(b), today=date.today().isoformat(), programs=load_programs(),
                                              open_lines=_open_lines(e), ballot=_ballot(code)))


def _cancel_ballots(item, who):
    """Cancel the board's email vote on this item, if there is one, so it can be asked again later."""
    try:
        import ballots_store as BS
        if not BS.enabled():
            return
        b = BS.current(item)
        if b and b['status'] != 'cancelled':
            BS.update(b['id'], status='cancelled', recorded_by=who)
    except Exception:
        pass


@bp.route('/event/<path:code>/draft', methods=['POST'])
@login_required
def to_draft(code):
    """Put a budget back into draft: off the board's list, any approval cleared and any open vote cancelled. Before the
    board approves it, whoever can edit it may do this; once approved, only the RE, ARE or treasurer."""
    if (g := _guard()):
        return g
    b = B.get(code) if B.enabled() else None
    from auth import user as _signed_in
    who = (_signed_in() or {}).get('name') or session.get('who', '') or 'Unknown'
    if not b:
        flash('That budget has not been saved yet.', 'warning')
    elif b.get('approved') and not officer_current():
        flash('The board approved this budget, so only the RE, ARE or treasurer can put it back into draft.', 'warning')
    else:
        B.set_draft(code, f"{date.today().isoformat()} by {who}")
        _cancel_ballots(f"budget:{str(b.get('date', ''))[:4]}:{code}", who)
        log_audit_event('BUDGET_DRAFT', who, {'event_code': code, 'was_approved': b.get('approved', '')})
        flash('Back in draft. It is off the board\'s list until you mark it ready.', 'info')
    return redirect(url_for('bud.detail', code=code))


@bp.route('/event/<path:code>/ready', methods=['POST'])
@login_required
def to_ready(code):
    """Take a budget out of draft so it is back on the board's list."""
    if (g := _guard()):
        return g
    from auth import user as _signed_in
    who = (_signed_in() or {}).get('name') or session.get('who', '') or 'Unknown'
    if B.enabled() and B.get(code):
        B.set_draft(code, '')
        log_audit_event('BUDGET_READY', who, {'event_code': code})
        flash('Ready for the board. It is on the board\'s list again.', 'success')
    return redirect(url_for('bud.detail', code=code))


def _ballot(code):
    """The latest board email vote on this budget (not cancelled), or None."""
    try:
        import ballots_store as BS
        if not BS.enabled():
            return None
        bs = [x for x in BS.ballots() if x['item'].startswith('budget:') and x['item'].endswith(':' + code)
              and x['status'] != 'cancelled']
        return max(bs, key=lambda x: x['id']) if bs else None
    except Exception:
        return None


def _open_lines(e):
    """Plan lines this event could be tied to (its program and year, not tied to another event)."""
    if not e or e.get('program_budget') or not P.enabled() or not e.get('date'):
        return []
    return P.open_lines(e['date'][:4], e.get('program_code'), load_events())


@bp.route('/event/<path:code>/tie', methods=['POST'])
@login_required
def tie(code):
    """Tie the event to one of its program's annual plan lines."""
    e = _event(code)
    sort = request.form.get('plan_line', '')
    from auth import user as _signed_in   # the audit log names the signed-in person, not a form field
    who = (_signed_in() or {}).get('name') or session.get('who', '') or 'Unknown'
    if e and sort:
        if P.link(e['date'][:4], e['program_code'], float(sort), code):
            item = (P.line_for(code) or {}).get('item', '')
            log_audit_event('PLAN_LINKED', who, {'event_code': code, 'year': e['date'][:4], 'program': e['program_code'], 'item': item})
            flash(f'Tied to the annual plan line "{item}".', 'success')
    return redirect(url_for('bud.detail', code=code))


def ops_budget_row(year):
    """Operations' budget (its annual plan) for the approvals list, shaped like a budget row, or None when there is no
    saved plan or it is approved. It is recorded on the plan (plan_store.set_approved), not in the Event Budgets sheet."""
    if not P.enabled():
        return None
    p = P.plan(year).get('OPS')
    if not p or not p['lines'] or not p['info'].get('updated'):
        return None
    st = P.plan_status(p['info'])
    if st in ('approved', 'draft'):
        return None
    b = {'event_code': 'OPS', 'event_name': f'Operations budget {year}', 'date': str(year), 'program': 'OPS',
         'approved': p['info'].get('approved', '')}
    return {'b': b, 'status': 'changed' if st == 'changed' else 'budget', 'surplus': p['net'], 'plan': p['net'],
            'savings': p.get('savings') or 0, 'equipment': round(-sum(ln['net'] or 0 for ln in p['lines'] if str(ln.get('account', '')).startswith('Capital Equipment')), 2),
            'link': url_for('plan.detail', year=year, program='OPS')}


HOW = {'meeting': 'board meeting', 'email': 'email vote'}


def _how_vote(f):
    """'Name (email vote, 7-0)' for the approved_by column, from the form's how and vote fields."""
    how = HOW.get(f.get('how', ''), '')
    vote = re.sub(r'[^0-9 -]', '', f.get('vote', '')).strip()
    bits = ', '.join(x for x in (how, vote) if x)
    return bits


def _pending_rows(year, only=''):
    plan = P.plan(year) if P.enabled() else {}
    if only:
        plan = {k: v for k, v in plan.items() if k == only}
    plan_by_code = {ln['event_code']: ln['net'] for x in plan.values() for ln in x['lines'] if ln['event_code']}
    rows = [{'b': b, 'status': _status(b), 'surplus': ((b['totals'].get('budget') or {}).get('surplus')), 'plan': plan_by_code.get(code)}
            for code, b in B.budgets().items() if str(b.get('date', '')).startswith(year) and _status(b) not in ('approved', 'draft')
            and (not only or b.get('program') == only)]
    rows.sort(key=lambda r: (r['b'].get('program', ''), r['b'].get('date', '')))
    if not only or only == 'OPS':
        if (ops := ops_budget_row(year)):
            rows.append(ops)
    return rows


def budget_vote_lines(year, codes):
    """Email lines for these budgets (name, date, program, expected result, link). Returns (lines, rows)."""
    rows = [r for r in _pending_rows(year) if r['b']['event_code'] in codes]
    lines = []
    for r in rows:
        b = r['b']
        link = r.get('link') or url_for('bud.detail', code=b['event_code'], _external=True)
        if link.startswith('/'):
            link = request.host_url.rstrip('/') + link
        money = '' if r['surplus'] is None else f", expected {'profit' if r['surplus'] >= 0 else 'loss'} ${abs(r['surplus']):,.2f}"
        if r.get('equipment'):
            run = r['surplus'] + r['equipment']
            money = (f", income minus running costs {'+' if run >= 0 else '-'}${abs(run):,.0f}, plus ${r['equipment']:,.0f} of equipment to buy"
                     f" (total ${abs(r['surplus']):,.0f} {'surplus' if r['surplus'] >= 0 else 'out'})")
        changed = ' (changed since approved ' + b['approved'] + ')' if r['status'] == 'changed' else ''
        when = '' if str(b.get('date', '')) in b['event_name'] else f", {b.get('date', '')}"
        prog = load_programs().get(b.get('program', ''), '')
        lines.append(f"- Budget: {b['event_name']}{when}{f' ({prog})' if prog else ''}{money}{changed}\n  {link}")
    return lines, rows


def record_budgets(year, picked, approved, how):
    """Record the board's approval of these budgets (event codes, and 'OPS' for Operations). Returns how many."""
    who = officer_current()['name']
    rec = f"{who} ({how})" if how else who
    ops = 'OPS' in picked and ops_budget_row(year) is not None
    codes = [c for c in picked if c != 'OPS' and B.get(c)]
    if codes:
        B.set_approved(codes, approved, rec)
    for c in codes:
        log_audit_event('BUDGET_APPROVED', who, {'event_code': c, 'approved': approved, 'how': how})
    if ops:
        P.set_approved(year, 'OPS', approved, rec)
        log_audit_event('PLAN_APPROVED', who, {'year': year, 'program': 'OPS', 'approved': approved, 'how': how})
    return len(codes) + ops


def pending_years():
    """Years with a budget not approved yet (event budgets, and the Operations budget in its plan)."""
    ys = {str(b.get('date', ''))[:4] for b in B.budgets().values() if str(b.get('date', ''))[:4].isdigit() and _status(b) != 'approved'}
    this = date.today().year
    ys |= {str(y) for y in range(this - 1, this + 3) if ops_budget_row(str(y))}
    return sorted(ys)


@bp.route('/approve', methods=['GET', 'POST'])
@login_required
def approve_many():
    """GET: the list lives in the Board app. POST (the API's approve_budgets): record one approval for several budgets."""
    year = request.args.get('year') or str(date.today().year)
    if request.method == 'GET':
        return redirect(url_for('board.home'))
    if not officer_current():
        flash('Recording a board decision is for the RE, ARE or treasurer.', 'warning')
        return redirect(url_for('board.home'))
    try:
        approved = date.fromisoformat(request.form.get('approved', '')).isoformat()
    except ValueError:
        flash('The approval date must be a date.', 'danger')
        return redirect(url_for('board.home'))
    picked = request.form.getlist('code')
    if not picked:
        flash('Tick at least one budget.', 'warning')
        return redirect(url_for('board.home'))
    n = record_budgets(year, picked, approved, _how_vote(request.form))
    flash(f'Board approval on {approved} recorded for {n} budget{"s" if n != 1 else ""}.', 'success')
    return redirect(url_for('board.home'))


@bp.route('/event/<path:code>/approve', methods=['POST'])
@login_required
@officer_required
def approve(code):
    if (g := _guard()):
        return g
    if not B.get(code):
        flash('There is no budget to approve.', 'warning')
        return redirect(url_for('bud.detail', code=code))
    who = officer_current()['name']
    approved = request.form.get('approved', '').strip()
    if approved:
        try:
            approved = date.fromisoformat(approved).isoformat()
        except ValueError:
            flash('The approval date must be a date.', 'danger')
            return redirect(url_for('bud.detail', code=code))
    how = _how_vote(request.form) if approved else ''
    B.set_approved(code, approved, f"{who} ({how})" if how else who)
    log_audit_event('BUDGET_APPROVED' if approved else 'BUDGET_UNAPPROVED', who, {'event_code': code, 'approved': approved, 'how': how})
    flash(f'Board approval recorded: {approved}.' if approved else 'Board approval cleared.', 'success')
    return redirect(url_for('bud.detail', code=code))


@bp.route('/event/<path:code>/delete', methods=['POST'])
@login_required
def delete(code):
    """Delete a budget saved by mistake. Anyone signed in may delete one the board has not approved; once approved, it
    stands for a board decision, so only an officer (signed in by email) may. Its files stay in Drive."""
    if (g := _guard()):
        return g
    b = B.get(code)
    if not b:
        flash('There is no budget to delete.', 'warning')
        return redirect(url_for('bud.detail', code=code))
    if b.get('approved') and not officer_current():
        flash('The board approved this budget, so only the RE, ARE or treasurer can delete it.', 'warning')
        return redirect(url_for('bud.detail', code=code))
    who = (officer_current() or {}).get('name') or session.get('who', '') or 'Unknown'
    B.delete(code)
    log_audit_event('BUDGET_DELETED', who, {'event_code': code, 'approved': b.get('approved', ''),
                                            'surplus': (b['totals'].get('budget') or {}).get('surplus'), 'lines': len(b['lines'])})
    flash('Budget deleted. Its files, if any, are still in Drive.', 'success')
    if (pg := program_of(code)):
        return redirect(url_for('plan.detail', year=pg[1], program=pg[0]))
    return redirect(url_for('bud.detail', code=code))


@bp.route('/event/<path:code>/actuals', methods=['GET', 'POST'])
@login_required
def actuals(code):
    """What the event really took in and spent, line by line, any time. Not a change to the budget: an approved
    budget stays approved. Next year, copying this budget puts these into "Last year actual"."""
    if (g := _guard()):
        return g
    e, b = _event(code), B.get(code)
    if not e or not b:
        flash('There is no budget for this event yet.', 'warning')
        return redirect(url_for('bud.detail', code=code))
    if request.method == 'POST':
        f = request.form
        lines, errors = [], []
        for ln, act in zip(b['lines'], f.getlist('act')):
            v = _money(act)
            if v == 'bad':
                errors.append(f'"{ln["description"]}": the actual must be a number.')
                continue
            lines.append(dict(ln, actual=v))
        for section, key in SECTIONS:      # lines that were not budgeted
            for desc, act in zip(f.getlist(f'new_{key}_desc'), f.getlist(f'new_{key}_act')):
                v = _money(act)
                if not desc.strip() and v is None:
                    continue
                if v == 'bad':
                    errors.append(f'"{desc or section}": the actual must be a number.')
                    continue
                lines.append({'section': section, 'description': desc.strip() or section, 'last_year': None, 'budget': None, 'actual': v})
        if errors:
            for m in errors:
                flash(m, 'danger')
            return redirect(url_for('bud.actuals', code=code))
        who = session.get('who') or 'Unknown'
        header = {k: b.get(k, '') for k in B.BUD_COLS if k not in ('updated', 'files_url', 'approved', 'approved_by')}
        header['updated_by'] = b.get('updated_by', '')
        lines.sort(key=lambda ln: ln['section'] != 'Income')
        B.save(header, lines, actuals_only=True)
        log_audit_event('BUDGET_ACTUALS_SAVED', who, {'event_code': code, 'lines': sum(1 for ln in lines if ln['actual'] is not None)})
        flash('Reported results saved.', 'success')
        return redirect(url_for('bud.detail', code=code))
    return render_template('bud.html', **_ctx(view='actuals', code=code, e=e, b=b, q=B.qbo_actuals().get(code)))


@bp.route('/event/<path:code>/edit', methods=['GET', 'POST'])
@login_required
def edit(code):
    if (g := _guard()):
        return g
    e = _event(code)
    if not e:
        flash('Add the event first; budgets hang off its #DR code.', 'warning')
        return redirect(url_for('bud.home'))
    b = B.get(code)
    if request.method == 'POST':
        f = request.form
        who = f.get('entered_by', '').strip() or session.get('who', '') or 'Unknown'
        session['who'] = who
        lines, errors = [], []
        for section, key in SECTIONS:
            for desc, ly, bud, act in zip(f.getlist(f'{key}_desc'), f.getlist(f'{key}_ly'), f.getlist(f'{key}_bud'), f.getlist(f'{key}_act')):
                vals = {'last_year': _money(ly), 'budget': _money(bud), 'actual': _money(act)}
                if not desc.strip() and all(v is None for v in vals.values()):
                    continue
                if 'bad' in vals.values():
                    errors.append(f'"{desc or section}": amounts must be numbers.')
                    continue
                lines.append({'section': section, 'description': desc.strip() or section, **vals})
        if errors:
            for msg in errors:
                flash(msg, 'danger')
            return render_template('bud.html', **_ctx(view='edit', code=code, e=e, form=f, lines=_form_lines(f), funds=_funds(code),
                                                      ly_events=_last_year_events(code, e, f.get('last_year_event', '')), pick=f.get('last_year_event', ''),
                                                      link=bool(f.get('link_last_year')),
                                                      plan=_plan_line(code))), 400
        ly = f.get('last_year_event', '').strip() if f.get('link_last_year') else ''
        if ly and ly not in {c['code'] for c in _last_year_events(code, e, (b or {}).get('last_year_event', ''))}:
            ly = ''          # only last year's events (or the one already linked)
        header = {'event_code': code, 'event_name': e.get('event_name', ''), 'program': e.get('program_code', ''), 'date': e.get('date', ''),
                  'chair': f.get('chair', '').strip(), 'location': f.get('location', '').strip(), 'notes': f.get('notes', '').strip(),
                  'updated_by': who, 'last_year_event': ly}
        B.save(header, lines, request.files.getlist('files'))
        log_audit_event('BUDGET_SAVED', who, {'event_code': code, 'lines': len(lines)})
        flash('Budget saved.', 'success')
        return redirect(url_for('bud.detail', code=code))
    # GET: the saved budget, or a copy of another event's to start from (its budget becomes this budget, any actuals
    # typed on it become "last year actual", and its QuickBooks results are shown above), or a blank form.
    src = request.args.get('copy')
    pick, link = '', False
    if src and (sb := B.get(src)):
        # Copy (also over a saved budget, after the form asked): nothing is saved until Save.
        # Its budget is the starting budget; "Last year actual" only gets actuals typed on it (often none: QuickBooks
        # has the real results, shown above the form).
        lines = [{'section': ln['section'], 'description': ln['description'], 'last_year': ln['actual'],
                  'budget': ln['budget'], 'actual': None} for ln in sb['lines']]
        form = dict(b) if b else {'chair': sb.get('chair', ''), 'location': sb.get('location', '')}
        # The event copied from stays chosen; linked unless the box was unticked (and only if it can be linked).
        pick = src
        link = request.args.get('link') != '0' and src in {c['code'] for c in _last_year_events(code, e)}
    elif b:
        lines, form = b['lines'], b
        pick = b.get('last_year_event', '')
        link = bool(pick)
    elif (pg := program_of(code)):
        lines, form = [], {'chair': O.director(pg[0])[0]}
    else:
        lines, form = [], {}
    copied = bool(src and B.get(src))
    return render_template('bud.html', **_ctx(view='edit', code=code, e=e, form=form, lines=lines, funds=_funds(code),
                                              ly_events=_last_year_events(code, e, pick), pick=pick, link=link, copied=copied, plan=_plan_line(code), src=B.get(src) if copied else None,
                                              src_q=_short_accounts(B.qbo_actuals().get(src)) if copied else None))


def _short_accounts(q):
    """QuickBooks actuals with account names cut to the useful part ("Club RX Rallycross Site Rental" -> "Site Rental")."""
    if not q:
        return None
    lines = [dict(ln, label=re.sub(r'^Club \w+ (Autocross|Rallycross|Road Racing|Road Rally|Street Survival|General Events?)\s*', '', ln['account']) or ln['account'])
             for ln in q['lines']]
    return dict(q, lines=lines)


def _form_lines(f):
    out = []
    for section, key in SECTIONS:
        for desc, ly, bud, act in zip(f.getlist(f'{key}_desc'), f.getlist(f'{key}_ly'), f.getlist(f'{key}_bud'), f.getlist(f'{key}_act')):
            out.append({'section': section, 'description': desc, 'last_year': ly, 'budget': bud, 'actual': act})
    return out


@bp.route('/qbo-refresh', methods=['POST'])
@login_required
def qbo_refresh():
    """Pull fresh actuals from QuickBooks now instead of waiting for the nightly sync."""
    who = session.get('who', '')
    if request_qbo_refresh(who):
        log_audit_event('QBO_REFRESH', who, {})
        flash('Getting the latest from QuickBooks. It takes about a minute; reload the page then.', 'info')
    else:
        flash('A QuickBooks refresh is already on its way.', 'info')
    return redirect(local_path(request.form.get('next')) or url_for('bud.home'))
