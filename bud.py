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
import plan_store as P
from utils import load_events, load_programs, log_audit_event, login_required

bp = Blueprint('bud', __name__, url_prefix='/events')

SECTIONS = (('Income', 'inc'), ('Expense', 'exp'))


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Events", who=session.get('who', ''), budgets_on=B.enabled(),
                months=['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], **kw)


def _guard():
    if not B.enabled():
        flash('Event budgets need the Google Sheets storage (STORAGE_BACKEND=sheets and BUDGETS_SHEET_ID).', 'warning')
        return redirect(url_for('home'))
    return None


def _code(e):
    return e.get('custom_tag') or e.get('unique_code')


def _event(code):
    return next((e for e in load_events() if _code(e) == code), None)


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
    events = load_events()
    plan_years = P.years() if P.enabled() else []
    years = sorted({e.get('date', '')[:4] for e in events if e.get('date', '')[:4].isdigit()} | set(plan_years) | {str(date.today().year)},
                   reverse=True)
    year = request.args.get('year') or str(date.today().year)
    buds = B.budgets() if B.enabled() else {}
    qbo = B.qbo_actuals() if B.enabled() else {}
    plan = P.plan(year) if P.enabled() else {}
    # ?program=RX: one program's view (a director's page): its events, plan, totals and open funds requests.
    only = request.args.get('program', '')
    if only:
        plan = {k: v for k, v in plan.items() if k == only}
    plan_by_code = {ln['event_code']: ln['net'] for x in plan.values() for ln in x['lines'] if ln['event_code']}
    rows = []
    year_events = sorted((e for e in events if e.get('date', '').startswith(year) and (not only or e.get('program_code') == only)),
                         key=lambda e: e.get('date', ''))
    codes = {_code(e) for e in year_events}
    for e in year_events:
        b = buds.get(_code(e))
        t = b['totals'] if b else {}
        q = qbo.get(_code(e))
        pc = e.get('program_code', '')
        rows.append({'e': e, 'code': _code(e), 'program': pc, 'date': e.get('date', ''), 'name': e.get('event_name', ''),
                     'b': b, 'status': _status(b), 'budget': (t.get('budget') or {}).get('surplus'),
                     'actual': q['totals']['surplus'] if q else None, 'plan': plan_by_code.get(_code(e)),
                     'unplanned': bool(plan.get(pc, {}).get('lines')) and _code(e) not in plan_by_code,
                     # The QuickBooks project's own name once it exists; else the name to give it (as on the event page).
                     'qbo_name': q['project'] if q and q.get('project') else f"{e.get('date', '')[:4]} {pc} {e.get('event_name', '')} {_code(e)}"})
    # Plan lines with a month and no registered event yet: the events the director expects but has not registered.
    # A line with no month (supplies, trophies) is program spending, not an event, and stays on the "other" row,
    # as do lines for months already past (planned, never registered).
    today = date.today()
    first = 1 if int(year) > today.year else 13 if int(year) < today.year else today.month
    for pc, p in plan.items():
        if pc == 'OPS':
            continue
        for ln in p['lines']:
            if ln['month'] and ln['month'] >= first and ln['event_code'] not in codes and not P.placeholder(ln['item']):
                rows.append({'e': None, 'code': '', 'program': pc, 'date': f"{year}-{ln['month']:02d}", 'name': plan_name(ln['item']),
                             'b': None, 'status': 'planned', 'budget': None, 'actual': None, 'plan': ln['net'],
                             'register': register_url(year, pc, ln)})
    rows.sort(key=lambda r: r['date'])
    evs = [r for r in rows if r['e']]
    summary = {'events': len(evs), 'missing': sum(1 for r in evs if not r['b']), 'planned': len(rows) - len(evs),
               'budget': sum(r['budget'] or 0 for r in evs), 'actual': sum(r['actual'] or 0 for r in evs),
               'as_of': next((q['as_of'] for q in qbo.values()), '')}
    programs = load_programs()
    # QuickBooks money on a program's accounts that is not in any of its event projects (season trophies, last
    # season's fees), so a program's actual here matches the Annual Plan. Programs with QuickBooks money but no events
    # (Operations: membership, storage, insurance) get a group too, so the page total is the region total.
    prog_act = P.actuals(year)[0] if P.enabled() and int(year) <= date.today().year else {}
    if only:
        prog_act = {k: v for k, v in prog_act.items() if k == only}
    names = dict(programs, OPS='Operations')
    groups = []
    for pc in sorted({r['program'] for r in rows} | {pc for pc, a in prog_act.items() if abs(a['net']) >= 0.5}
                     | {pc for pc, p in plan.items() if p.get('lines')}, key=lambda pc: (pc == 'OPS', names.get(pc, pc))):
        g = [r for r in rows if r['program'] == pc]
        events_net = sum(r['actual'] or 0 for r in g)
        other = round(prog_act[pc]['net'] - events_net, 2) if pc in prog_act else 0
        other = other if abs(other) >= 0.5 else 0
        gp = plan.get(pc, {})
        gplan = gp.get('net') if gp.get('lines') else None
        groups.append({'code': pc, 'name': names.get(pc, pc or 'Other'), 'rows': g, 'other': other,
                       'events': sum(1 for r in g if r['e']), 'planned': sum(1 for r in g if not r['e']),
                       'missing': sum(1 for r in g if r['e'] and not r['b']),
                       'budget': sum(r['budget'] or 0 for r in g), 'actual': round(events_net + other, 2), 'plan': gplan,
                       'other_plan': round(gplan - sum(r['plan'] or 0 for r in g), 2) if gplan is not None else None})
    summary['other'] = sum(g['other'] for g in groups)
    summary['actual'] = round(summary['actual'] + summary['other'], 2)
    funds = [r for r in F.requests() if r['status'] == 'Submitted' and r['program'] == only] if only and F.enabled() else []
    return render_template('bud.html', **_ctx(view='list', groups=groups, year=year, years=years, s=summary,
                                              programs=programs, plan_on=P.enabled(), only=only, names=names, funds=funds))


@bp.route('/event/<path:code>')
@login_required
def detail(code):
    e = _event(code)
    b = B.get(code) if B.enabled() else None
    if not e and not b:
        flash('Event not found.', 'warning')
        return redirect(url_for('bud.home'))
    return render_template('bud.html', **_ctx(view='detail', code=code, e=e or {}, b=b, funds=_funds(code),
                                              q=B.qbo_actuals().get(code) if B.enabled() else None, plan=_plan_line(code),
                                              status=_status(b), today=date.today().isoformat(), programs=load_programs(),
                                              open_lines=_open_lines(e)))


def _open_lines(e):
    """Plan lines this event could be tied to (its program and year, not tied to another event)."""
    if not e or not P.enabled() or not e.get('date'):
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


@bp.route('/approve', methods=['GET', 'POST'])
@login_required
def approve_many():
    """After a board meeting: tick the budgets the board approved and record them all with one meeting date."""
    if (g := _guard()):
        return g
    year = request.args.get('year') or str(date.today().year)
    if request.method == 'POST':
        f = request.form
        who = f.get('who', '').strip() or session.get('who', '') or 'Unknown'
        session['who'] = who
        codes = [c for c in f.getlist('code') if B.get(c)]
        try:
            approved = date.fromisoformat(f.get('approved', '')).isoformat()
        except ValueError:
            flash('The meeting date must be a date.', 'danger')
            return redirect(url_for('bud.approve_many', year=year))
        if not codes:
            flash('Tick at least one budget.', 'warning')
            return redirect(url_for('bud.approve_many', year=year))
        B.set_approved(codes, approved, who)
        for c in codes:
            log_audit_event('BUDGET_APPROVED', who, {'event_code': c, 'approved': approved})
        flash(f'Board approval on {approved} recorded for {len(codes)} budget{"s" if len(codes) != 1 else ""}.', 'success')
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
    return render_template('bud.html', **_ctx(view='approve', rows=rows, year=year, today=date.today().isoformat()))


@bp.route('/event/<path:code>/approve', methods=['POST'])
@login_required
def approve(code):
    if (g := _guard()):
        return g
    if not B.get(code):
        flash('There is no budget to approve.', 'warning')
        return redirect(url_for('bud.detail', code=code))
    who = request.form.get('who', '').strip() or session.get('who', '') or 'Unknown'
    session['who'] = who
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
                  'chair': f.get('chair', '').strip(), 'location': f.get('location', '').strip(), 'notes': f.get('notes', '').strip(),
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
        form = {'chair': sb.get('chair', ''), 'location': sb.get('location', '')}
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
    lines = [dict(ln, label=re.sub(r'^Club \w+ (Autocross|Rallycross|Road Racing|Road Rally|General Events?)\s*', '', ln['account']) or ln['account'])
             for ln in q['lines']]
    return dict(q, lines=lines)


def _form_lines(f):
    out = []
    for section, key in SECTIONS:
        for desc, ly, bud, act in zip(f.getlist(f'{key}_desc'), f.getlist(f'{key}_ly'), f.getlist(f'{key}_bud'), f.getlist(f'{key}_act')):
            out.append({'section': section, 'description': desc, 'last_year': ly, 'budget': bud, 'actual': act})
    return out
