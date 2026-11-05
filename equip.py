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
from itsdangerous import URLSafeTimedSerializer

import equip_store as E
import officials as O
from utils import local_path, log_audit_event, login_required

bp = Blueprint('equip', __name__, url_prefix='/operations')

LINK_DAYS = 30
SOON_DAYS = 90
NEXT_DAYS = 30  # the Overview's coming-up window


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
    """{email: name} allowed to edit: the equipment committee and the officers (full access to every app), from the
    officials list only."""
    import access
    return {o['email'].lower(): o['name'] for o in O.all() if 'ops' in access.perms(o.get('role', '')) and o.get('email')}


def editor_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        ed = session.get('equip_editor')
        if not ed or ed.get('email', '').lower() not in editors():
            session.pop('equip_editor', None)
            flash('Changes are made by the equipment committee and the club officers.', 'warning')
            return redirect(url_for('equip.home'))
        return f(*a, **kw)
    return wrapped


def _changed(f, cols, base_row):
    """Which fields the person changed in the form. The form carries the values it was opened with (_base), so a field
    left alone is not written back over an edit made in the sheet since. Old forms without _base: every field sent."""
    import json
    try:
        base = json.loads(f.get('_base') or 'null')
    except ValueError:
        base = None
    sent = [k for k in cols if k in f and k != 'id']
    if not isinstance(base, dict):
        return set(sent)
    return {k for k in sent if E.norm(f.get(k, '')) != E.norm(base.get(k, ''))}


def _diff(before, after):
    """Fields a quick action changed on a row."""
    return {k for k in after if k != 'id' and E.norm(after.get(k)) != E.norm((before or {}).get(k))}


def _who():
    return (session.get('equip_editor') or {}).get('name') or session.get('who') or 'Unknown'


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='equipment-editor')


@bp.route('/signin', methods=['GET', 'POST'])
@bp.route('/signin/<token>')
def signin(token=None):
    """Old separate sign-in: everyone now signs in once by email code, and the role comes from the officials list."""
    flash('This needs the equipment committee or an officer, signed in with their own email address.', 'warning')
    return redirect(url_for('equip.home'))


@bp.route('/signout', methods=['POST'])
def signout():
    session.pop('equip_editor', None)
    return redirect(url_for('equip.home'))


@bp.route('/')
@login_required
def home():
    """The highlights: what needs attention now, what is coming up in the next 30 days, and the four numbers that matter."""
    if (g := _guard()):
        return g
    today = date.today()
    t_iso = today.isoformat()
    month = (today + timedelta(days=NEXT_DAYS)).isoformat()
    soon = (today + timedelta(days=SOON_DAYS)).isoformat()
    tasks, places, items = E.tasks(), E.places(), [i for i in E.items() if i['status'] != 'Gone']
    todo = [t for t in tasks if not E.is_auto(t)]
    overdue = sorted([t for t in todo if t['due'] and t['due'] < t_iso], key=lambda t: t['due'])
    bills = E.recurring_costs(places, tasks)
    # The next 30 days, to-dos and automatic card charges together, by date. Monthly charges are left out: they are every month.
    auto = {t['id'] for t in tasks if E.is_auto(t)}
    coming = sorted([{'date': t['due'], 'what': t['task'], 'about': t['about'], 'kind': 'todo', 't': t} for t in todo if t['due'] and t_iso <= t['due'] <= month]
                    + [{'date': _roll(b, t_iso), 'what': b['what'], 'about': b['paid_how'], 'kind': 'bill', 'amount': b['each'], 'b': b}
                       for b in bills if b['id'] in auto and (b['every'] or 0) > 1 and t_iso <= _roll(b, t_iso) <= month and not (b.get('ends') and _roll(b, t_iso) > b['ends'])],
                    key=lambda r: r['date'])
    rp = E.reserve_plan(items, today)
    big = [i for i in items if i['category'] in ('Vehicle', 'Trailer') and i['status'] != 'Planned']
    for i in big:
        i['lc'] = E.lifecycle(i, today)
    replace_soon = sorted([i for i in big if i['lc'] and i['lc'].get('replace') and i['lc']['replace'] <= today.year + 1], key=lambda i: i['lc']['replace'])
    import equip_docs as D
    docs = D.status(E.documents())
    return render_template('equip.html', **_ctx(view='home', today=t_iso, overdue=overdue, coming=coming,
                                                n_due=sum(1 for t in todo if t['due'] and t['due'] <= soon), undated=[t for t in todo if not t['due'] and not t['last_done']],
                                                docs_missing=sum(len(r['missing']) for r in docs), docs_total=sum(len(D.needed(r)) for r in docs),
                                                bills_year=sum(b['per_year'] for b in bills), n_bills=len(bills), rp=rp,
                                                n_vehicles=sum(1 for i in big if i['category'] == 'Vehicle'), n_trailers=sum(1 for i in big if i['category'] == 'Trailer'),
                                                replace_soon=replace_soon))


def _roll(b, today):
    """An automatic charge's next date on or after today; the sheet's date can be a past one nobody moved on."""
    d = b['next']
    for _ in range(240):
        if not d or d >= today:
            break
        d = E.next_due({'due': d, 'repeat_months': b['every']})
    return d or ''


@bp.route('/vehicles')
@login_required
def vehicles():
    """Vehicles and trailers the region has (planned purchases are on Replacement): where each one is kept, its condition, value left and when to replace it."""
    if (g := _guard()):
        return g
    today = date.today()
    big = sorted([i for i in E.items() if i['status'] not in ('Gone', 'Planned') and i['category'] in ('Vehicle', 'Trailer')], key=lambda i: (i['category'], i['name']))
    import equip_docs as D
    docs = {r['for_id']: r for r in D.status([d for d in E.documents() if d['for_id']])}
    for i in big:
        i['lc'] = E.lifecycle(i, today)
        d = docs.get(i['id'])
        i['docs'] = {'have': len(d['have']), 'missing': d['missing'], 'error': d['error']} if d else \
            {'have': 0, 'missing': D.needed({'needed': D.DEFAULT_NEEDED.get(i['category'], '')}), 'error': ''}
    return render_template('equip.html', **_ctx(view='vehicles', big=big))


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


def _docs_for(rid, kind=None, name=''):
    """The item's or place's Documents row with what is there; a blank one (no folder yet) for a vehicle, trailer or
    place, so its page can still offer an upload."""
    import equip_docs as D
    rows = [d for d in E.documents() if d['for_id'] == rid]
    if rows:
        d = D.status(rows)[0]
    elif kind in D.DEFAULT_NEEDED:
        need = D.DEFAULT_NEEDED[kind]
        d = {'id': '', 'for_id': rid, 'for_name': name, 'folder': '', 'needed': need, 'notes': '', 'have': [], 'others': [], 'error': '',
             'missing': D.needed({'needed': need})}
    else:
        return None
    d['kinds'] = D.kinds_for(d)
    return d


@bp.route('/items/new', methods=['GET', 'POST'])
@bp.route('/items/<item_id>', methods=['GET', 'POST'])
@login_required
def item(item_id=None):
    if (g := _guard()):
        return g
    it = next((i for i in E.items() if i['id'] == item_id), None) if item_id else {}
    if item_id and not it:
        abort(404)
    if not item_id and request.method == 'GET':
        # "Add item here" and "Add a planned purchase" start the form filled in.
        it = {k: request.args[k] for k in ('place', 'status', 'category') if request.args.get(k)}
    if request.method == 'POST':
        return _save_item(it)
    edit = request.args.get('edit') == '1' or not item_id
    if edit and not _is_editor():
        return editor_required(lambda: None)()
    lc = E.lifecycle(it) if item_id else None
    tasks = [t for t in E.tasks() if it and t['about'] == it.get('name')]
    return render_template('equip.html', **_ctx(view='item', it=it, lc=lc, edit=edit, locations=_location_names(), tasks=tasks,
                                                docs=_docs_for(it['id'], it.get('category'), it.get('name', '')) if it and it.get('id') and not edit else None, today=date.today().isoformat()))


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
        E.save('Property', row, changed=_diff(it, row))
        log_audit_event('EQUIP_ITEM_LOCATED', _who(), {'id': it['id'], 'name': it['name'], 'place': place, 'was': it.get('place', '')})
        flash(f"{it['name']}: {'moved to ' + place if place != it.get('place') else 'confirmed at ' + place}.", 'success')
        return redirect(url_for('equip.item', item_id=it['id']))
    if f.get('action') in ('remove', 'restore'):
        gone = f['action'] == 'remove'
        stamp = f"{'Removed' if gone else 'Put back'} {date.today().isoformat()} by {_who()}."
        row.update(status='Gone' if gone else 'In use', notes=((row.get('notes') or '') + '\n' + stamp).strip(),
                   updated=date.today().isoformat(), updated_by=_who())
        E.save('Property', row, changed=_diff(it, row))
        log_audit_event('EQUIP_ITEM_REMOVED' if gone else 'EQUIP_ITEM_RESTORED', _who(), {'id': it['id'], 'name': it['name']})
        flash(f"{it['name']} {'removed. It stays on record as Gone' if gone else 'is back in use'}.", 'success')
        return redirect(local_path(f.get('back')) or url_for('equip.item', item_id=it['id']))
    if f.get('action') == 'replacement':
        # The Replacement page's quick edit: just the year and the cost estimate.
        for k in ('replace_year', 'replace_cost'):
            row[k] = f.get(k, '').replace('$', '').replace(',', '').strip()
        row.update(updated=date.today().isoformat(), updated_by=_who())
        E.save('Property', row, changed=_diff(it, row))
        log_audit_event('EQUIP_ITEM_SAVED', _who(), {'id': it['id'], 'name': it['name'], 'replace_year': row['replace_year'], 'replace_cost': row['replace_cost']})
        flash(f"{it['name']}: replacement updated.", 'success')
        return redirect(url_for('equip.plan') + f"#{it['id']}")
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
    ch = _changed(f, E.ITEM_COLS, it) | {'updated', 'updated_by'} | ({'seen'} if row.get('seen') != (it or {}).get('seen') else set())
    rid = E.save('Property', row, changed=None if new else ch)
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
    for r in rows:
        r['kinds'] = D.kinds_for(r)
    return render_template('equip.html', **_ctx(view='documents', kinds=[(k, r) for k, r in kinds if r], n_missing=sum(len(r['missing']) for r in rows),
                                                error=next((r['error'] for r in rows if r['error']), ''),
                                                root=os.environ.get('OPERATIONS_DOCS_FOLDER_URL', '')))


@bp.route('/documents/add', methods=['POST'])
@login_required
@editor_required
def add_document():
    """Upload scans or photos of a document straight into the right Drive folder, named so they count. A vehicle, trailer
    or place with no documents folder yet gets one (and a Documents row) on its first upload."""
    if (g := _guard()):
        return g
    import equip_docs as D
    from storage import drive_folder
    f = request.form
    back = local_path(f.get('back')) or url_for('equip.documents')
    files = [x for x in request.files.getlist('files') if x and x.filename]
    doc = (f.get('other') if f.get('doc') == '_other' else f.get('doc') or '').strip()
    if not files or not doc:
        flash('Choose what the document is and pick a file or take a photo.', 'warning')
        return redirect(back)
    rows = E.documents()
    row = next((r for r in rows if r['id'] and r['id'] == f.get('row_id')), None) \
        or next((r for r in rows if r['for_id'] and r['for_id'] == f.get('for_id')), None)
    if not row:
        rid = f.get('for_id', '')
        it = next((i for i in E.items() if i['id'] == rid), None) if rid.startswith('EQ') else None
        p = next((x for x in E.places() if x['id'] == rid), None) if rid.startswith('PL') else None
        if not (it or p):
            abort(404)
        root = D.folder_id(os.environ.get('OPERATIONS_DOCS_FOLDER_URL', ''))
        if not root:
            flash('No Documents folder is set up in Drive (OPERATIONS_DOCS_FOLDER_URL).', 'warning')
            return redirect(back)
        name = it['name'] if it else p['name']
        folder = drive_folder(name, root)
        row = {'for_id': rid, 'for_name': name, 'folder': folder['webViewLink'],
               'needed': D.DEFAULT_NEEDED.get(it['category'] if it else 'place', ''), 'notes': ''}
        row['id'] = E.save('Documents', row)
        log_audit_event('EQUIP_DOCS_FOLDER_MADE', _who(), {'id': row['id'], 'for': rid, 'name': name})
    folder = D.folder_id(row['folder'])
    if not folder:
        flash(f"{row['for_name']} has no Drive folder link on the Documents tab.", 'warning')
        return redirect(back)
    names = []
    for n, x in enumerate(files, 1):
        name = D.file_name(doc, row['for_name'], f.get('note', ''), x.filename, n if len(files) > 1 else 1)
        D.upload(folder, name, x, _who())
        names.append(name)
    D.refresh()
    log_audit_event('EQUIP_DOCS_UPLOADED', _who(), {'id': row['id'], 'for': row['for_name'], 'doc': doc, 'files': names})
    flash(f"Saved {doc} for {row['for_name']}" + (f' ({len(names)} files)' if len(names) > 1 else '') + '.', 'success')
    return redirect(back)


@bp.route('/places/new', methods=['GET', 'POST'])
@bp.route('/places/<place_id>', methods=['GET', 'POST'])
@login_required
def place(place_id=None):
    if (g := _guard()):
        return g
    p = next((x for x in E.places(closed=True) if x['id'] == place_id), None) if place_id else {}
    if place_id and not p:
        abort(404)
    if request.method == 'POST':
        return _save_place(p)
    edit = request.args.get('edit') == '1' or not place_id
    if edit and not _is_editor():
        return editor_required(lambda: None)()
    held = [i for i in E.items() if p and i['place'] == p.get('name') and i['status'] != 'Gone']
    back = request.args.get('back', '')
    return render_template('equip.html', **_ctx(view='place', p=p, edit=edit, held=held, back=local_path(back),
                                                docs=_docs_for(p['id'], 'place', p.get('name', '')) if p and p.get('id') and not edit else None))


@editor_required
def _save_place(p):
    f = request.form
    row = dict(p or {})
    if f.get('action') in ('close', 'reopen'):
        held = [i for i in E.items() if i['place'] == p.get('name') and i['status'] != 'Gone']
        if f['action'] == 'close' and held:
            flash(f"Move the {len(held)} item{'s' if len(held) != 1 else ''} kept at {p['name']} first (open each and use Where is it now?).", 'warning')
            return redirect(url_for('equip.place', place_id=p['id']))
        row.update(status='Closed' if f['action'] == 'close' else '', updated=date.today().isoformat(), updated_by=_who())
        E.save('Storage', row, changed=_diff(p, row))
        log_audit_event('EQUIP_PLACE_CLOSED' if f['action'] == 'close' else 'EQUIP_PLACE_REOPENED', _who(), {'id': p['id'], 'name': p['name']})
        flash(f"{p['name']} {'closed: it no longer shows in Gear or Bills' if f['action'] == 'close' else 'is in use again'}.", 'success')
        return redirect(url_for('equip.items') if f['action'] == 'close' else url_for('equip.place', place_id=p['id']))
    for k in E.PLACE_COLS:
        if k in f:
            row[k] = f.get(k, '').strip()
    if not row.get('name'):
        flash('Give the place a name.', 'danger')
        return redirect(request.url)
    row.update(updated=date.today().isoformat(), updated_by=_who())
    pid = E.save('Storage', row, changed=None if not row.get('id') else _changed(f, E.PLACE_COLS, p) | {'updated', 'updated_by'})
    log_audit_event('EQUIP_PLACE_SAVED', _who(), {'id': pid, 'name': row['name']})
    flash(f"Saved {row['name']}.", 'success')
    back = f.get('back', '')
    return redirect(local_path(back) or url_for('equip.place', place_id=pid))


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
    return render_template('equip.html', **_ctx(view='task', t=t, names=names, frequencies=E.FREQUENCIES, back=local_path(back),
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
        E.save('Recurring costs and to-dos', row, changed=_diff(t, row))
        log_audit_event('EQUIP_TASK_DONE', _who(), {'id': t['id'], 'task': t['task'], 'next': nxt})
        flash(f"Marked done: {t['task']}." + (f" Next due {nxt}." if nxt else ''), 'success')
        back = f.get('back', '')
        return redirect(local_path(back) or url_for('equip.tasks'))
    for k in E.TASK_COLS:
        if k in f:
            row[k] = f.get(k, '').strip()
    if 'auto' in f:
        row['auto'] = 'yes' if 'yes' in f.getlist('auto') else ''    # a hidden blank field sits before the checkbox
    if not row.get('task'):
        flash('Say what has to be done.', 'danger')
        return redirect(request.url)
    ch = _changed(f, E.TASK_COLS, t) - {'auto'}
    if 'auto' in f and E.norm(row.get('auto')) != E.norm((t or {}).get('auto')):
        ch.add('auto')
    tid = E.save('Recurring costs and to-dos', row, changed=None if not row.get('id') else ch)
    log_audit_event('EQUIP_TASK_SAVED', _who(), {'id': tid, 'task': row['task'], 'due': row.get('due', '')})
    flash('Saved.', 'success')
    back = f.get('back', '')
    return redirect(local_path(back) or url_for('equip.tasks'))


@bp.route('/report.pdf')
@login_required
def report_pdf():
    """The Operations report: every section of these pages, laid out for printing."""
    if (g := _guard()):
        return g
    import equip_docs as D
    import equip_pdf
    from flask import Response
    docs = D.status(E.documents())
    data = equip_pdf.build(E.items(), E.places(), E.tasks(), docs=None if any(r['error'] for r in docs) else docs)
    name = f'DRSCCA Operations report {date.today().isoformat()}.pdf'
    return Response(data, mimetype='application/pdf', headers={'Content-Disposition': f'inline; filename="{name}"'})


@bp.route('/one-pager.pdf')
def one_pager():
    return redirect(url_for('equip.report_pdf'), 301)


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
    motion = None
    try:
        import motions_store as MS
        if MS.enabled():
            motion = next((m for m in MS.motions() if 'replacement' in m['title'].lower() and m['status'] != 'withdrawn'), None)
    except Exception:
        motion = None
    return render_template('equip.html', **_ctx(view='plan', rp=rp, by_year=by_year, missing=missing, motion=motion))


def overdue_count():
    """For the Treasurer page."""
    today = date.today().isoformat()
    return sum(1 for t in E.tasks() if t['due'] and t['due'] < today and not E.is_auto(t)) if E.enabled() else 0
