"""Operations: everything the region pays for or looks after between events. Recurring costs (insurance,
storage, subscriptions, registrations, inspections), property (vehicles, trailers, gear), where it is kept, and
what is due when, on one page. Records live in the Registry sheet (equip_store).

Everyone signed in can look. Only equipment committee members (role "Equipment" in the read-only officials list,
plus the treasurer) can change anything, and only after signing in through a link emailed to that address, so the
shared app password alone cannot change the region's records. The records are a plain spreadsheet in the shared
drive (equip_store), so the region can keep them up by hand without the app.
"""
import os
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

import equip_store as E
import officials as O
from utils import log_audit_event, login_required, send_mail

bp = Blueprint('equip', __name__, url_prefix='/operations')

LINK_DAYS = 30
SOON_DAYS = 90


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Operations", editor=session.get('equip_editor'),
                categories=E.CATEGORIES, statuses=E.STATUSES, cost_categories=E.COST_CATEGORIES, every_label=E.every_label, accounts=E.ACCOUNTS, budget_year=budget_year(), **kw)


def budget_year():
    """The Operations budget people are working on: next year's from September (when plans are built), else this year's."""
    today = date.today()
    return today.year + 1 if today.month >= 9 else today.year


def _guard():
    if not E.enabled():
        flash('Operations needs the Google Sheets storage (STORAGE_BACKEND=sheets and OPERATIONS_SHEET_ID).', 'warning')
        return redirect(url_for('home'))
    return None


def editors():
    """{email: name} allowed to edit: the equipment committee and the treasurer, from the officials list only."""
    return {o['email'].lower(): o['name'] for o in O.all() if o.get('role') in ('Equipment', 'Treasurer') and o.get('email')}


def editor_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        ed = session.get('equip_editor')
        if not ed or ed.get('email', '').lower() not in editors():
            session.pop('equip_editor', None)
            flash('Making changes needs a sign-in link sent to your committee email.', 'warning')
            return redirect(url_for('equip.signin', next=request.full_path.rstrip('?')))
        return f(*a, **kw)
    return wrapped


def _who():
    return (session.get('equip_editor') or {}).get('name') or session.get('who') or 'Unknown'


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='equipment-editor')


@bp.route('/signin', methods=['GET', 'POST'])
@login_required
def signin():
    if request.method == 'POST':
        mail = request.form.get('email', '').strip().lower()
        nxt = request.form.get('next', '')
        eds = editors()
        if mail in eds:
            token = _serializer().dumps({'email': mail, 'name': eds[mail], 'next': nxt if nxt.startswith('/') else ''})
            link = f"{request.url_root.rstrip('/')}{url_for('equip.signin_link', token=token)}"
            send_mail('Your sign-in link for DRSCCA Operations',
                      f"Use this link to make changes in the region's Operations records (good for {LINK_DAYS} days, on the device "
                      f"you open it on):\n\n{link}\n\nIf you did not ask for it, ignore this email.", mail)
        # Same answer either way, so the page does not reveal who is on the committee.
        flash('If that address is on the equipment committee, a sign-in link is on its way.', 'info')
        return redirect(url_for('equip.home'))
    return render_template('equip.html', **_ctx(view='signin', nxt=request.args.get('next', '')))


@bp.route('/signin/<token>')
@login_required
def signin_link(token):
    try:
        t = _serializer().loads(token, max_age=LINK_DAYS * 86400)
    except BadSignature:
        flash('That sign-in link has expired or is not valid. Ask for a new one.', 'warning')
        return redirect(url_for('equip.signin'))
    if t['email'] not in editors():
        abort(403)
    session['equip_editor'] = {'email': t['email'], 'name': t['name']}
    log_audit_event('EQUIP_SIGNIN', t['name'], {'email': t['email']})
    flash(f"Signed in to make changes as {t['name']}.", 'success')
    return redirect(t.get('next') or url_for('equip.home'))


@bp.route('/signout', methods=['POST'])
def signout():
    session.pop('equip_editor', None)
    return redirect(url_for('equip.home'))


@bp.route('/')
@login_required
def home():
    """The one page: what needs doing, what the bills come to, and the fleet at a glance."""
    if (g := _guard()):
        return g
    today = date.today()
    tasks, places, items = E.tasks(), E.places(), [i for i in E.items() if i['status'] != 'Gone']
    soon = (today + timedelta(days=SOON_DAYS)).isoformat()
    todo = [t for t in tasks if not E.is_auto(t)]
    due = [t for t in todo if t['due'] and t['due'] <= soon]
    bills = E.recurring_costs(places, tasks)
    rp = E.reserve_plan(items, today)
    big = sorted([i for i in items if i['category'] in ('Vehicle', 'Trailer')], key=lambda i: (i['category'], i['name']))
    for i in big:
        i['lc'] = E.lifecycle(i, today)
    import equip_docs as D
    docs = D.status(E.documents())
    return render_template('equip.html', **_ctx(view='home', today=today.isoformat(), due=due, undated=[t for t in todo if not t['due'] and not t['last_done']],
                                                docs_missing=sum(len(r['missing']) for r in docs), docs_total=sum(len(D.needed(r)) for r in docs),
                                                late=sum(1 for t in due if t['due'] < today.isoformat()), bills_year=sum(b['per_year'] for b in bills),
                                                n_bills=len(bills), rp=rp, big=big))


@bp.route('/items')
@login_required
def items():
    """Property, by where it is (the default) or by category."""
    if (g := _guard()):
        return g
    rows = E.items()
    places = E.places()
    if request.args.get('by') != 'category':
        where, people, other, unknown = E.locations(rows, places)
        return render_template('equip.html', **_ctx(view='where', where=where, people=people, other=other, unknown=unknown,
                                                    n_live=sum(1 for i in rows if i['status'] not in ('Gone', 'Planned'))))
    show_gone = request.args.get('gone') == '1'
    rows = [i for i in rows if show_gone or i['status'] != 'Gone']
    for i in rows:
        i['lc'] = E.lifecycle(i)
    groups = [(c, [i for i in rows if i['category'] == c]) for c in E.CATEGORIES + ['']]
    return render_template('equip.html', **_ctx(view='items', groups=[(c or 'Uncategorized', g) for c, g in groups if g], show_gone=show_gone))


def _location_names():
    """What "Kept at" can say: storage places, then the things other items are kept in, then people."""
    rows = [i for i in E.items() if i['status'] != 'Gone']
    held = {i['place'][5:].strip() for i in rows if i['place'].lower().startswith('with ')}
    return ([p['name'] for p in E.places()] + sorted({i['nickname'] or i['name'] for i in rows if i['category'] in ('Vehicle', 'Trailer')})
            + [f'With {n}' for n in sorted(held)])


def _docs_for(rid):
    import equip_docs as D
    rows = [d for d in E.documents() if d['for_id'] == rid]
    return D.status(rows)[0] if rows else None


@bp.route('/items/new', methods=['GET', 'POST'])
@bp.route('/items/<item_id>', methods=['GET', 'POST'])
@login_required
def item(item_id=None):
    if (g := _guard()):
        return g
    it = next((i for i in E.items() if i['id'] == item_id), None) if item_id else {}
    if item_id and not it:
        abort(404)
    if request.method == 'POST':
        return _save_item(it)
    edit = request.args.get('edit') == '1' or not item_id
    if edit and not _is_editor():
        return editor_required(lambda: None)()
    lc = E.lifecycle(it) if it else None
    tasks = [t for t in E.tasks() if it and t['about'] == it.get('name')]
    return render_template('equip.html', **_ctx(view='item', it=it, lc=lc, edit=edit, locations=_location_names(), tasks=tasks,
                                                docs=_docs_for(it['id']) if it and not edit else None, today=date.today().isoformat()))


def _is_editor():
    ed = session.get('equip_editor')
    return bool(ed and ed.get('email', '').lower() in editors())


@editor_required
def _save_item(it):
    f = request.form
    row = dict(it or {})
    if f.get('action') == 'located':
        # Confirm where it is (or say where it went): the quick form on the item page.
        place = f.get('place', '').strip()
        row.update(place=place, seen=date.today().isoformat(), updated=date.today().isoformat(), updated_by=_who())
        E.save('Property', row)
        log_audit_event('EQUIP_ITEM_LOCATED', _who(), {'id': it['id'], 'name': it['name'], 'place': place, 'was': it.get('place', '')})
        flash(f"{it['name']}: {'moved to ' + place if place != it.get('place') else 'confirmed at ' + place}.", 'success')
        return redirect(url_for('equip.item', item_id=it['id']))
    if 'place' in f and f.get('place', '').strip() != (it or {}).get('place', ''):
        row['seen'] = date.today().isoformat()
    for k in E.ITEM_COLS:
        if k in f:
            row[k] = f.get(k, '').strip()
    if not row.get('name'):
        flash('Give it a name.', 'danger')
        return redirect(request.url)
    row.update(updated=date.today().isoformat(), updated_by=_who())
    new = not row.get('id')
    rid = E.save('Property', row)
    log_audit_event('EQUIP_ITEM_SAVED', _who(), {'id': rid, 'name': row['name'], 'new': new})
    flash(f"Saved {row['name']}.", 'success')
    return redirect(url_for('equip.item', item_id=rid))


@bp.route('/places')
@login_required
def places():
    """Storage places are the sections of Property, by location."""
    return redirect(url_for('equip.items'))


@bp.route('/documents')
@login_required
def documents():
    """Titles, registrations, insurance cards and agreements: which the region has in Drive and which are missing."""
    if (g := _guard()):
        return g
    import equip_docs as D
    if request.args.get('fresh'):
        D.refresh()
    rows = D.status(E.documents())
    kinds = [('Insurance and region', [r for r in rows if not r['for_id']]),
             ('Vehicles and trailers', [r for r in rows if r['for_id'].startswith('EQ')]),
             ('Storage places', [r for r in rows if r['for_id'].startswith('PL')])]
    return render_template('equip.html', **_ctx(view='documents', kinds=[(k, r) for k, r in kinds if r], n_missing=sum(len(r['missing']) for r in rows),
                                                error=next((r['error'] for r in rows if r['error']), ''),
                                                root=os.environ.get('OPERATIONS_DOCS_FOLDER_URL', '')))


@bp.route('/places/new', methods=['GET', 'POST'])
@bp.route('/places/<place_id>', methods=['GET', 'POST'])
@login_required
def place(place_id=None):
    if (g := _guard()):
        return g
    p = next((x for x in E.places() if x['id'] == place_id), None) if place_id else {}
    if place_id and not p:
        abort(404)
    if request.method == 'POST':
        return _save_place(p)
    edit = request.args.get('edit') == '1' or not place_id
    if edit and not _is_editor():
        return editor_required(lambda: None)()
    held = [i for i in E.items() if p and i['place'] == p.get('name') and i['status'] != 'Gone']
    back = request.args.get('back', '')
    return render_template('equip.html', **_ctx(view='place', p=p, edit=edit, held=held, back=back if back.startswith('/') else '',
                                                docs=_docs_for(p['id']) if p and not edit else None))


@editor_required
def _save_place(p):
    f = request.form
    row = dict(p or {})
    for k in E.PLACE_COLS:
        if k in f:
            row[k] = f.get(k, '').strip()
    if not row.get('name'):
        flash('Give the place a name.', 'danger')
        return redirect(request.url)
    row.update(updated=date.today().isoformat(), updated_by=_who())
    pid = E.save('Storage', row)
    log_audit_event('EQUIP_PLACE_SAVED', _who(), {'id': pid, 'name': row['name']})
    flash(f"Saved {row['name']}.", 'success')
    back = f.get('back', '')
    return redirect(back if back.startswith('/') else url_for('equip.place', place_id=pid))


@bp.route('/tasks')
@login_required
def tasks():
    """Things a person has to do, grouped by when. Bills charged automatically are on Bills, not here."""
    if (g := _guard()):
        return g
    today = date.today()
    soon = (today + timedelta(days=SOON_DAYS)).isoformat()
    rows = [t for t in E.tasks() if not E.is_auto(t)]
    groups = [('Overdue', [t for t in rows if t['due'] and t['due'] < today.isoformat()]),
              ('Next 90 days', [t for t in rows if t['due'] and today.isoformat() <= t['due'] <= soon]),
              ('Later', [t for t in rows if t['due'] and t['due'] > soon]),
              ('No date yet', [t for t in rows if not t['due']])]
    return render_template('equip.html', **_ctx(view='tasks', groups=[(n, g) for n, g in groups if g], today=today.isoformat()))


@bp.route('/tasks/new', methods=['GET', 'POST'])
@bp.route('/tasks/<task_id>', methods=['GET', 'POST'])
@login_required
def task(task_id=None):
    if (g := _guard()):
        return g
    t = next((x for x in E.tasks() if x['id'] == task_id), None) if task_id else {}
    if task_id and not t:
        abort(404)
    if request.method == 'POST':
        return _save_task(t)
    if not _is_editor():
        return editor_required(lambda: None)()
    names = sorted({i['name'] for i in E.items() if i['category'] in ('Vehicle', 'Trailer')} | {p['name'] for p in E.places()})
    back = request.args.get('back', '')
    return render_template('equip.html', **_ctx(view='task', t=t, names=names, frequencies=E.FREQUENCIES, back=back if back.startswith('/') else '',
                                                bill=request.args.get('bill') == '1' or bool(t.get('cost'))))


@editor_required
def _save_task(t):
    f = request.form
    row = dict(t or {})
    if f.get('action') == 'done':
        # Done: note who and when, and move a repeating task to its next date (a one-time task stays, marked done).
        row.update(last_done=date.today().isoformat(), done_by=_who())
        nxt = E.next_due(t)
        row['due'] = nxt
        E.save('Recurring costs and to-dos', row)
        log_audit_event('EQUIP_TASK_DONE', _who(), {'id': t['id'], 'task': t['task'], 'next': nxt})
        flash(f"Marked done: {t['task']}." + (f" Next due {nxt}." if nxt else ''), 'success')
        back = f.get('back', '')
        return redirect(back if back.startswith('/') else url_for('equip.tasks'))
    for k in E.TASK_COLS:
        if k in f:
            row[k] = f.get(k, '').strip()
    if 'auto' in f:
        row['auto'] = 'yes' if 'yes' in f.getlist('auto') else ''    # a hidden blank field sits before the checkbox
    if not row.get('task'):
        flash('Say what has to be done.', 'danger')
        return redirect(request.url)
    tid = E.save('Recurring costs and to-dos', row)
    log_audit_event('EQUIP_TASK_SAVED', _who(), {'id': tid, 'task': row['task'], 'due': row.get('due', '')})
    flash('Saved.', 'success')
    back = f.get('back', '')
    return redirect(back if back.startswith('/') else url_for('equip.tasks'))


@bp.route('/one-pager.pdf')
@login_required
def one_pager():
    """Operations at a glance, one printable page."""
    if (g := _guard()):
        return g
    import equip_pdf
    from flask import Response
    data = equip_pdf.build(E.items(), E.places(), E.tasks())
    return Response(data, mimetype='application/pdf', headers={'Content-Disposition': 'inline; filename="DRSCCA Operations at a glance.pdf"'})


@bp.route('/bills')
@login_required
def costs():
    """Every bill the region pays on a schedule (storage, insurance, subscriptions, registrations), by category."""
    if (g := _guard()):
        return g
    rows = E.recurring_costs(E.places(), E.tasks())
    for r in rows:
        r['how_often'] = E.every_label(r['every'])
    cats = [(c, [r for r in rows if r['category'] == c]) for c in E.COST_CATEGORIES]
    return render_template('equip.html', **_ctx(view='costs', cats=[(c, r) for c, r in cats if r], total=sum(r['per_year'] for r in rows),
                                                today=date.today().isoformat(), plan_year=date.today().year + 1))


@bp.route('/costs')
def old_costs():
    return redirect(url_for('equip.costs'))


@bp.route('/plan')
@login_required
def plan():
    """Replacement plan: when each long-lived item reaches the end of its life, and the yearly amount to set aside."""
    if (g := _guard()):
        return g
    rows = [i for i in E.items() if i['status'] != 'Gone']
    rp = E.reserve_plan(rows)
    missing = [i for i in rows if E.is_capital(i) and not E.lifecycle(i) and i['status'] != 'Retire']
    by_year = {}
    for r in rp['near']:
        by_year[r['year']] = by_year.get(r['year'], 0) + r['cost']
    return render_template('equip.html', **_ctx(view='plan', rp=rp, by_year=by_year, missing=missing))


def overdue_count():
    """For the Treasurer page."""
    today = date.today().isoformat()
    return sum(1 for t in E.tasks() if t['due'] and t['due'] < today and not E.is_auto(t)) if E.enabled() else 0
