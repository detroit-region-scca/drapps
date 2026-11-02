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
from utils import load_events, load_programs, log_audit_event, login_required

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
        return round(float(v), 2)
    except ValueError:
        return 'bad'


def _plan_line(code):
    """The annual plan line this event is tied to, or None."""
    return P.line_for(code) if P.enabled() else None


def _status(b):
    """'approved', 'changed' (edited after approval), 'budget' or None."""
    if not b:
        return None
    if b.get('approved'):
        return 'changed' if b.get('updated', '') > b['approved'] else 'approved'
    return 'budget'


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
                     'needs_budget': B.enabled() and _code(e) not in buds and days is not None and days >= 0,
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
                             'days': None, 'past': False, 'needs_budget': False, 'register': register_url(year, pc, ln)})
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
    return render_template('bud.html', **_ctx(view='detail', code=code, e=e or {}, b=b, funds=_funds(code),
                                              q=q, plan=plan,
                                              status=_status(b), today=date.today().isoformat(), programs=load_programs(),
                                              open_lines=_open_lines(e)))


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
    who = request.form.get('who', '').strip() or session.get('who', '') or 'Unknown'
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
    if st == 'approved':
        return None
    b = {'event_code': 'OPS', 'event_name': f'Operations budget {year}', 'date': str(year), 'program': 'OPS',
         'approved': p['info'].get('approved', '')}
    return {'b': b, 'status': 'changed' if st == 'changed' else 'budget', 'surplus': p['net'], 'plan': p['net'],
            'link': url_for('plan.detail', year=year, program='OPS')}


@bp.route('/approve', methods=['GET', 'POST'])
@login_required
@officer_required
def approve_many():
    """After a board meeting: tick the budgets the board approved and record them all with one meeting date."""
    if (g := _guard()):
        return g
    year = request.args.get('year') or str(date.today().year)
    if request.method == 'POST':
        f = request.form
        who = officer_current()['name']
        ops = 'OPS' in f.getlist('code') and ops_budget_row(year) is not None
        codes = [c for c in f.getlist('code') if c != 'OPS' and B.get(c)]
        try:
            approved = date.fromisoformat(f.get('approved', '')).isoformat()
        except ValueError:
            flash('The meeting date must be a date.', 'danger')
            return redirect(url_for('bud.approve_many', year=year))
        if not codes and not ops:
            flash('Tick at least one budget.', 'warning')
            return redirect(url_for('bud.approve_many', year=year))
        if codes:
            B.set_approved(codes, approved, who)
        for c in codes:
            log_audit_event('BUDGET_APPROVED', who, {'event_code': c, 'approved': approved})
        if ops:
            P.set_approved(year, 'OPS', approved, who)
            log_audit_event('PLAN_APPROVED', who, {'year': year, 'program': 'OPS', 'approved': approved})
        n = len(codes) + ops
        flash(f'Board approval on {approved} recorded for {n} budget{"s" if n != 1 else ""}.', 'success')
        return redirect(url_for('bud.home', year=year))
    plan = P.plan(year) if P.enabled() else {}
    # ?program=RX: one program's view (a director's page): its events, plan, totals and open funds requests.
    only = request.args.get('program', '')
    if only:
        plan = {k: v for k, v in plan.items() if k == only}
    plan_by_code = {ln['event_code']: ln['net'] for x in plan.values() for ln in x['lines'] if ln['event_code']}
    rows = [{'b': b, 'status': _status(b), 'surplus': ((b['totals'].get('budget') or {}).get('surplus')), 'plan': plan_by_code.get(code)}
            for code, b in B.budgets().items() if str(b.get('date', '')).startswith(year) and _status(b) != 'approved']
    rows.sort(key=lambda r: (r['b'].get('program', ''), r['b'].get('date', '')))
    if not only or only == 'OPS':
        if (ops := ops_budget_row(year)):
            rows.append(ops)
    this = date.today().year
    years = sorted({str(this - 1), str(this), str(this + 1)} | {str(b.get('date', ''))[:4] for b in B.budgets().values()
                                                                if str(b.get('date', ''))[:4].isdigit()}, reverse=True)
    return render_template('bud.html', **_ctx(view='approve', rows=rows, year=year, years=years, today=date.today().isoformat()))


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
    B.set_approved(code, approved, who)
    log_audit_event('BUDGET_APPROVED' if approved else 'BUDGET_UNAPPROVED', who, {'event_code': code, 'approved': approved})
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
                                                      plan=_plan_line(code))), 400
        header = {'event_code': code, 'event_name': e.get('event_name', ''), 'program': e.get('program_code', ''), 'date': e.get('date', ''),
                  'chair': f.get('chair', '').strip(), 'chair_email': f.get('chair_email', '').strip().lower(),
                  'location': f.get('location', '').strip(), 'notes': f.get('notes', '').strip(),
                  'updated_by': who}
        B.save(header, lines, request.files.getlist('files'))
        log_audit_event('BUDGET_SAVED', who, {'event_code': code, 'lines': len(lines)})
        flash('Budget saved.', 'success')
        return redirect(url_for('bud.detail', code=code))
    # GET: the saved budget, or a copy of another event's to start from (its budget becomes this budget, any
    # actuals typed on its old form become "last year", and its QuickBooks results are shown above), or a blank form.
    src = request.args.get('copy')
    if b:
        lines, form = b['lines'], b
    elif src and (sb := B.get(src)):
        lines = [{'section': ln['section'], 'description': ln['description'], 'last_year': ln['actual'],
                  'budget': ln['budget'], 'actual': None} for ln in sb['lines']]
        form = {'chair': sb.get('chair', ''), 'chair_email': sb.get('chair_email', ''), 'location': sb.get('location', '')}
    elif (pg := program_of(code)):
        lines, form = [], {'chair': O.director(pg[0])[0]}
    else:
        lines, form = [], {}
    others = sorted((x for k, x in B.budgets().items() if k != code), key=lambda x: x.get('date', ''), reverse=True)
    copied = bool(src and not b and B.get(src))
    return render_template('bud.html', **_ctx(view='edit', code=code, e=e, form=form, lines=lines, others=others, funds=_funds(code),
                                              copied=copied, plan=_plan_line(code), src=B.get(src) if copied else None,
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
