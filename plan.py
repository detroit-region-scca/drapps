"""Annual Plan: each program's yearly plan (events and items, expected profit or cost) against QuickBooks actuals.

Directors fill in their own program's plan for the next year (the template is the edit page); the board
approves it in the minutes. The top of the page charts profit against the plan through the year, and Books check lists what
the nightly QuickBooks sync (tools/qbo_nightly.py) found for the treasurer.
"""
import os
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

import bud_store as B
import officials as O
import plan_store as P
from bud import register_url
from utils import load_events, load_programs, log_audit_event, login_required

bp = Blueprint('plan', __name__, url_prefix='/plan')

OPS = 'OPS'
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _programs():
    """[(code, name)] in a fixed order, Operations last."""
    return list(load_programs().items()) + [(OPS, 'Operations')]


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Annual Plan", who=session.get('who', ''),
                months=MONTHS, n_exceptions=len(P.exceptions()), **kw)


def _guard():
    if not P.enabled():
        flash('The annual plan needs the Google Sheets storage (STORAGE_BACKEND=sheets and PLAN_SHEET_ID).', 'warning')
        return redirect(url_for('home'))
    return None


def _year(y):
    if not str(y).isdigit() or not 2020 <= int(y) <= 2100:
        abort(404)
    return str(y)


def _money(v):
    v = str(v or '').replace(',', '').replace('$', '').replace('−', '-').strip()
    if not v:
        return None
    if v.startswith('(') and v.endswith(')'):
        v = '-' + v[1:-1]
    try:
        return round(float(v), 2)
    except ValueError:
        return 'bad'


def _code(e):
    return e.get('custom_tag') or e.get('unique_code')


def _budget_surplus(code):
    b = (B.budgets() if B.enabled() else {}).get(code)
    return ((b or {}).get('totals', {}).get('budget') or {}).get('surplus')


def _event_actuals(program, year):
    """The program's registered events that year with their budgeted surplus and QuickBooks profit (None until there)."""
    qbo = B.qbo_actuals() if B.enabled() else {}
    evs = [e for e in load_events() if e.get('program_code') == program and str(e.get('date', '')).startswith(year)]
    return [{'e': e, 'code': _code(e), 'budget': _budget_surplus(_code(e)), 'net': (qbo.get(_code(e)) or {}).get('totals', {}).get('surplus')}
            for e in sorted(evs, key=lambda e: e.get('date', ''))]


def _diff(actual, plan):
    return round(actual - plan, 2) if actual is not None and plan is not None else None


def _kids(year, program, p, program_actual, future):
    """A program's rows when expanded: its plan lines and registered events, matched on the event code, then
    whatever QuickBooks has on the program's accounts that is not in an event project, so the rows add up to
    the program total."""
    events = {x['code']: x for x in _event_actuals(program, year)}
    kids = []
    for ln in (p or {}).get('lines', []):
        x = events.pop(ln['event_code'], None) if ln['event_code'] else None
        kids.append({'name': ln['item'], 'date': x['e']['date'] if x else (f"{year}-{ln['month']:02d}" if ln['month'] else ''),
                     'code': ln['event_code'] if x else '', 'plan': ln['net'], 'budget': x['budget'] if x else None,
                     'actual': x['net'] if x else None, 'line': ln})
    for code, x in events.items():
        kids.append({'name': x['e'].get('event_name', ''), 'date': x['e']['date'], 'code': code, 'plan': None, 'budget': x['budget'],
                     'actual': x['net']})
    for k in kids:
        k['diff'] = _diff(k['actual'], k['plan'])
    if not future and program_actual is not None:
        other = round(program_actual - sum(k['actual'] or 0 for k in kids), 2)
        if abs(other) >= 0.5:
            kids.append({'name': 'Other, not in an event project', 'date': '', 'code': '', 'plan': None, 'budget': None, 'actual': other,
                         'diff': None, 'other': True})
    return sorted(kids, key=lambda k: (k.get('other', False), k['date'] or '9999'))


def _chart(year, plan, as_of):
    """Monthly plan and QuickBooks profit per program, for the plan vs actual chart. A plan line with no month
    is spread evenly over the year; the actual stops at the last month QuickBooks covers."""
    plan_m = {}
    for code, p in plan.items():
        m = plan_m.setdefault(code, [0.0] * 12)
        for ln in p['lines']:
            if ln['month']:
                m[ln['month'] - 1] += ln['net'] or 0
            else:
                for i in range(12):
                    m[i] += (ln['net'] or 0) / 12
    actual = P.monthly(year)
    today = date.today()
    last = 12 if int(year) < today.year else today.month if int(year) == today.year else 0
    r2 = lambda xs: [round(x, 2) for x in xs]  # noqa: E731
    return {'programs': [[c, n] for c, n in _programs()], 'plan': {c: r2(v) for c, v in plan_m.items()},
            'actual': {c: r2(v) for c, v in actual.items()}, 'last': last, 'as_of': as_of}


def summary(year, plan=None, act=None):
    """Each program's plan, budgeted and actual profit for a year, with its lines and events; and the totals.
    Shared by the Annual Plan page and the treasurer's report."""
    plan = plan if plan is not None else P.plan(year)
    act = act if act is not None else P.actuals(year)[0]
    future = int(year) > date.today().year
    rows = []
    for code, name in _programs():
        p, a = plan.get(code), act.get(code)
        pn = p['net'] if p and p['lines'] else None
        an = a['net'] if a else None
        kids = _kids(year, code, p, an, future)
        budgets = [k['budget'] for k in kids if k['budget'] is not None]
        rows.append({'code': code, 'name': name, 'plan': pn, 'actual': an, 'info': (p or {}).get('info', {}),
                     'budget': round(sum(budgets), 2) if budgets else None, 'diff': _diff(an, pn), 'kids': kids})
    return rows, {k: round(sum(r[k] or 0 for r in rows), 2) for k in ('plan', 'budget', 'actual')}


@bp.route('/')
@login_required
def home():
    if (g := _guard()):
        return g
    year = _year(request.args.get('year') or date.today().year)
    plan, (act, as_of) = P.plan(year), P.actuals(year)
    future = int(year) > date.today().year
    rows, tot = summary(year, plan, act)
    # In = a director saved it through the form (a line moved in by the treasurer is not a submitted plan).
    sent = sum(1 for r in rows if r['info'].get('updated'))
    chart = None if future else _chart(year, plan, as_of)
    return render_template('plan.html', **_ctx(view='home', year=year, years=P.years(), rows=rows, tot=tot, as_of=as_of,
                                               sent=sent, future=future, chart=chart))


@bp.route('/<year>/<program>')
@login_required
def detail(year, program):
    if (g := _guard()):
        return g
    year, names = _year(year), dict(_programs())
    if program not in names:
        abort(404)
    p = P.plan(year).get(program) or {'lines': [], 'net': 0.0, 'info': {}}
    act, as_of = P.actuals(year)
    qbo = B.qbo_actuals() if B.enabled() else {}
    for ln in p['lines']:
        ln['actual'] = (qbo.get(ln['event_code']) or {}).get('totals', {}).get('surplus') if ln['event_code'] else None
        ln['budget'] = _budget_surplus(ln['event_code']) if ln['event_code'] else None
        if not ln['event_code'] and program != OPS:
            # "Register event" opens Add event filled in from this line, tied to it once saved.
            ln['register'] = register_url(year, program, ln)
    prev = str(int(year) - 1)
    # One list: each plan line with its event, then registered events not in the plan, then the program's other money.
    rows = _kids(year, program, p, (act.get(program) or {}).get('net'), int(year) > date.today().year)
    # Plan against QuickBooks account by account, with last year's actual beside it for a future plan.
    accts = P.by_account(p['lines'], act.get(program))
    last = {r['account']: r['actual'] for r in P.by_account([], P.actuals(prev)[0].get(program))}
    for r in accts:
        r['last'] = last.get(r['account'])
    # A program planned by account rather than by event (Operations): one table grouped by account.
    by_account = bool(p['lines']) and all(not ln['event_code'] for ln in p['lines']) and any(ln.get('account') for ln in p['lines'])
    groups = _account_groups(p['lines'], accts) if by_account else []
    return render_template('plan.html', **_ctx(view='detail', year=year, program=program, name=names[program], p=p,
                                               a=act.get(program), as_of=as_of, events=_event_actuals(program, year),
                                               prev=prev, prev_lines=bool(P.plan(prev).get(program, {}).get('lines')),
                                               accts=accts, future=int(year) > date.today().year, now_year=date.today().year, rows=rows,
                                               groups=groups, last_net=(P.actuals(prev)[0].get(program) or {}).get('net')))


def _account_groups(lines, accts):
    """Plan lines grouped under their QuickBooks account, in reading order: income, running costs (largest first),
    equipment bought (Capital Equipment accounts, not an expense), then money set aside with no account. Accounts with
    actuals but no plan line are included so nothing is hidden."""
    out = []
    for r in accts:
        acc = r['account']
        kind = 'none' if not acc else 'capital' if acc.startswith('Capital Equipment') else \
            'income' if (r['plan'] or 0) > 0 or (r['plan'] is None and (r['actual'] or r['last'] or 0) > 0) else 'cost'
        out.append(dict(r, kind=kind, lines=[ln for ln in lines if (ln.get('account') or '') == acc]))
    order = {'income': 0, 'cost': 1, 'capital': 2, 'none': 3}
    return sorted(out, key=lambda g: (order[g['kind']], -abs(g['plan'] or 0), -abs(g['actual'] or g['last'] or 0)))


@bp.route('/<year>/<program>/edit', methods=['GET', 'POST'])
@login_required
def edit(year, program):
    if (g := _guard()):
        return g
    year, names = _year(year), dict(_programs())
    if program not in names:
        abort(404)
    prev = str(int(year) - 1)
    if request.method == 'POST':
        f = request.form
        who = f.get('entered_by', '').strip() or session.get('who', '') or 'Unknown'
        session['who'] = who
        lines, errors = [], []
        reg_events = {x['code']: x['e'] for x in _event_actuals(program, year)}
        for item, month, net, note, code, account in zip(f.getlist('item'), f.getlist('month'), f.getlist('net'), f.getlist('note'), f.getlist('code'),
                                                         f.getlist('account') or [''] * len(f.getlist('item'))):
            item, note, v = item.strip(), note.strip(), _money(net)
            if code in reg_events:
                # A line tied to a registered event takes the event's name and month; the event is the record.
                ev = reg_events[code]
                item, month = ev.get('event_name', '') or item, str(int(ev['date'][5:7])) if str(ev.get('date', ''))[5:7].isdigit() else month
            if not item and v is None and not note:
                continue
            if v == 'bad':
                errors.append(f'"{item or net}": the amount must be a number, like 1400 or -500.')
                continue
            if code and code in [ln['event_code'] for ln in lines]:
                errors.append(f'{code} is picked on two lines; an event goes with one plan line.')
            lines.append({'item': item or 'Other', 'month': int(month) if month.isdigit() else '', 'net': v, 'note': note, 'event_code': code,
                          'account': account})
        # Director and email come from the officials directory; the form does not set them.
        d_name, d_mail = O.director(program)
        info = {'director': d_name, 'email': d_mail, 'notes': f.get('notes', '').strip(),
                'updated_by': who}
        if errors:
            for msg in errors:
                flash(msg, 'danger')
            return render_template('plan.html', **_ctx(view='edit', year=year, prev=prev, program=program, name=names[program],
                                                       info=info, lines=lines, ref=_reference(program, prev),
                                                       reg=_event_actuals(program, year), accounts=_accounts(program, year))), 400
        P.save(year, program, info, lines)
        total = round(sum(ln['net'] or 0 for ln in lines), 2)
        log_audit_event('PLAN_SAVED', who, {'year': year, 'program': program, 'lines': len(lines), 'net': total})
        flash(f'{names[program]} plan for {year} saved.', 'success')
        return redirect(url_for('plan.detail', year=year, program=program))
    p = P.plan(year).get(program)
    copied = False
    if p and p['lines']:
        lines, info = p['lines'], p['info']
    else:
        # A new plan starts blank, or from last year's items and amounts (?start=copy) with the old year left out
        # of the names; the director and email carry over from last year either way.
        last = P.plan(prev).get(program) or {'lines': [], 'info': {}}
        lines = [] if request.args.get('start') != 'copy' else [
            {'item': P.clean_name(ln['item'], prev), 'month': ln['month'], 'net': ln['net'], 'note': '', 'event_code': ''}
            for ln in last['lines'] if not P.placeholder(ln['item'])]
        info = (p or {}).get('info') or {k: last['info'].get(k, '') for k in ('director', 'email')}
        copied = bool(lines)
    # Registered events with no plan line yet start as lines of their own, already tied, so the director only
    # adds the expected profit (the budget's surplus when there is one).
    reg = _event_actuals(program, year)
    taken = {ln['event_code'] for ln in lines if ln['event_code']}
    added = []
    for x in reg:
        if x['code'] in taken or not str(x['e'].get('date', ''))[5:7].isdigit():
            continue
        # A line copied from last year that is clearly this event ("Son of Sno*Drift XXVIII" for XXIX) becomes
        # its line: tied, renamed, last year's amount kept.
        scored = sorted(((P.match_score(ln['item'], ln['month'], x['e']), i) for i, ln in enumerate(lines) if not ln['event_code']), reverse=True)
        if copied and scored and scored[0][0] >= 5 and (len(scored) < 2 or scored[1][0] < scored[0][0]):
            lines[scored[0][1]].update(item=x['e'].get('event_name', ''), event_code=x['code'])
            continue
        added.append({'item': x['e'].get('event_name', ''), 'month': int(x['e']['date'][5:7]), 'net': x['budget'], 'note': '',
                      'event_code': x['code']})
    lines = sorted(list(lines) + added, key=lambda ln: ln['month'] or 13) if added else lines
    return render_template('plan.html', **_ctx(view='edit', year=year, prev=prev, program=program, name=names[program], info=info,
                                               lines=lines, copied=copied, added=len(added), ref=_reference(program, prev), reg=reg,
                                               accounts=_accounts(program, year)))


def _accounts(program, year):
    """The QuickBooks accounts this program's money goes to (from the last three years), for the plan form."""
    y = int(year)
    return P.accounts(program, [str(y - 2), str(y - 1), str(y)])


def _reference(program, year):
    """What the director sees beside the form: last year's QuickBooks totals and event results."""
    act, as_of = P.actuals(year)
    return {'year': year, 'a': act.get(program), 'as_of': as_of, 'events': [x for x in _event_actuals(program, year)]}


@bp.route('/checks', methods=['GET', 'POST'])
@login_required
def checks():
    if (g := _guard()):
        return g
    if request.method == 'POST':
        key = request.form.get('key', '')
        e = next((x for x in P.exceptions() if x['key'] == key), None)
        if e:
            who = request.form.get('who', '').strip() or session.get('who', '') or 'Unknown'
            session['who'] = who
            note = request.form.get('note', '').strip()
            P.mark_reviewed(key, who, note)
            log_audit_event('PLAN_REVIEWED', who, {'key': key, 'kind': e['kind'], 'detail': e['detail'], 'note': note})
            flash('Marked as looked at. It stays hidden unless something changes.', 'success')
        return redirect(url_for('plan.checks'))
    exc = P.exceptions()
    kinds = []
    for e in exc:
        if e['kind'] not in kinds:
            kinds.append(e['kind'])
    reviewed = [e for e in P.exceptions(include_reviewed=True) if e['reviewed']]
    return render_template('plan.html', **_ctx(view='checks', groups=[(k, [e for e in exc if e['kind'] == k]) for k in kinds],
                                               reviewed=reviewed, as_of=next((e['as_of'] for e in exc), '')))
