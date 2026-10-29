"""Event Budgets: the region budget form (last year, budget, actual) per event, kept in the Event Budgets sheet (bud_store)."""
import os
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import bud_store as B
import fr_store as F
from utils import load_events, load_programs, log_audit_event, login_required

bp = Blueprint('bud', __name__, url_prefix='/budgets')

SECTIONS = (('Income', 'inc'), ('Expense', 'exp'))


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Event Budgets", who=session.get('who', ''), **kw)


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


def _funds(code):
    if not F.enabled():
        return []
    return [r for r in F.requests() if r['event_code'] == code and r['status'] != 'Rejected']


@bp.route('/')
@login_required
def home():
    if (g := _guard()):
        return g
    years = sorted({e.get('date', '')[:4] for e in load_events() if e.get('date')}, reverse=True)
    year = request.args.get('year') or str(date.today().year)
    buds = B.budgets()
    qbo = B.qbo_actuals()
    fr_by = {}
    for r in (F.requests() if F.enabled() else []):
        if r['status'] != 'Rejected' and r['event_code']:
            fr_by[r['event_code']] = fr_by.get(r['event_code'], 0) + r['net']
    rows = []
    for e in sorted((e for e in load_events() if e.get('date', '').startswith(year)), key=lambda e: e.get('date', ''), reverse=True):
        b = buds.get(_code(e))
        t = b['totals'] if b else {}
        q = qbo.get(_code(e))
        rows.append({'e': e, 'code': _code(e), 'b': b, 'budget': (t.get('budget') or {}).get('surplus'),
                     'actual': q['totals']['surplus'] if q else None, 'funds': fr_by.get(_code(e), 0)})
    summary = {'events': len(rows), 'missing': sum(1 for r in rows if not r['b']),
               'budget': sum(r['budget'] or 0 for r in rows), 'actual': sum(r['actual'] or 0 for r in rows),
               'with_actuals': sum(1 for r in rows if r['actual'] is not None),
               'as_of': next((q['as_of'] for q in qbo.values()), '')}
    programs = load_programs()
    groups = []
    for pc in sorted({r['e'].get('program_code', '') for r in rows}, key=lambda pc: programs.get(pc, pc)):
        g = [r for r in rows if r['e'].get('program_code', '') == pc]
        groups.append({'code': pc, 'name': programs.get(pc, pc or 'Other'), 'rows': g,
                       'budget': sum(r['budget'] or 0 for r in g), 'actual': sum(r['actual'] or 0 for r in g),
                       'funds': sum(r['funds'] for r in g), 'missing': sum(1 for r in g if not r['b'])})
    return render_template('bud.html', **_ctx(view='list', groups=groups, year=year, years=years or [year], s=summary,
                                              programs=programs))


@bp.route('/event/<path:code>')
@login_required
def detail(code):
    if (g := _guard()):
        return g
    e = _event(code)
    b = B.get(code)
    if not e and not b:
        flash('Event not found.', 'warning')
        return redirect(url_for('bud.home'))
    return render_template('bud.html', **_ctx(view='detail', code=code, e=e or {}, b=b, funds=_funds(code),
                                              q=B.qbo_actuals().get(code)))


@bp.route('/event/<path:code>/edit', methods=['GET', 'POST'])
@login_required
def edit(code):
    if (g := _guard()):
        return g
    e = _event(code)
    if not e:
        flash('Register the event in Event Register first; budgets hang off its #DR code.', 'warning')
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
            return render_template('bud.html', **_ctx(view='edit', code=code, e=e, form=f, lines=_form_lines(f), funds=_funds(code))), 400
        header = {'event_code': code, 'event_name': e.get('event_name', ''), 'program': e.get('program_code', ''), 'date': e.get('date', ''),
                  'chair': f.get('chair', '').strip(), 'location': f.get('location', '').strip(), 'notes': f.get('notes', '').strip(),
                  'updated_by': who}
        B.save(header, lines, request.files.getlist('files'))
        log_audit_event('BUDGET_SAVED', who, {'event_code': code, 'lines': len(lines)})
        flash('Budget saved.', 'success')
        return redirect(url_for('bud.detail', code=code))
    # GET: the saved budget, or a copy of another event's to start from (its budget becomes this budget,
    # its actuals become "last year"), or a blank form.
    src = request.args.get('copy')
    if b:
        lines, form = b['lines'], b
    elif src and (sb := B.get(src)):
        lines = [{'section': ln['section'], 'description': ln['description'], 'last_year': ln['actual'] if ln['actual'] is not None else ln['budget'],
                  'budget': ln['budget'], 'actual': None} for ln in sb['lines']]
        form = {'chair': sb.get('chair', ''), 'location': sb.get('location', '')}
    else:
        lines, form = [], {}
    others = sorted((x for k, x in B.budgets().items() if k != code), key=lambda x: x.get('date', ''), reverse=True)
    return render_template('bud.html', **_ctx(view='edit', code=code, e=e, form=form, lines=lines, others=others, funds=_funds(code),
                                              copied=bool(src and not b)))


def _form_lines(f):
    out = []
    for section, key in SECTIONS:
        for desc, ly, bud, act in zip(f.getlist(f'{key}_desc'), f.getlist(f'{key}_ly'), f.getlist(f'{key}_bud'), f.getlist(f'{key}_act')):
            out.append({'section': section, 'description': desc, 'last_year': ly, 'budget': bud, 'actual': act})
    return out
