"""Operations: everything the region pays for or looks after between events. Recurring costs (insurance,
storage, subscriptions, registrations, inspections), property (vehicles, trailers, gear), where it is kept, and
what is due when, on one page. Records live in the Registry sheet (equip_store).

Everyone signed in can look. Only people whose role has "Edit Operations" (Admin > Roles: by default the Equipment
committee and the officers) can change anything; everyone signs in with their own email address, so the app knows
who they are. The records are a plain spreadsheet in the shared drive (equip_store), so the region can keep them up
by hand without the app.
"""
import os
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, abort, flash, g as flask_g, redirect, render_template, request, session, url_for

import equip_store as E
import officials as O
from officer import current as officer_current
from utils import local_path, log_audit_event, login_required

bp = Blueprint('equip', __name__, url_prefix='/operations')

SOON_DAYS = 90
NEXT_DAYS = 30  # the Overview's coming-up window


PAGE_TITLES = {'home': 'Overview', 'vehicles': 'Vehicles', 'where': 'Gear', 'items': 'Gear by category', 'all': 'Gear',
               'tasks': 'To do', 'costs': 'Bills', 'plan': 'Replacement'}
GEAR_VIEWS = [('all', 'All'), ('vehicles', 'Vehicles and trailers'), ('place', 'By place'), ('category', 'By category')]
BIG = ('Vehicle', 'Trailer')


def _ctx(**kw):
    kw.setdefault('page_title', PAGE_TITLES.get(kw.get('view'), ''))      # the browser tab: the page's or the record's name
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Operations", editor=session.get('equip_editor'),
                categories=E.CATEGORIES, statuses=E.STATUSES, cost_categories=E.COST_CATEGORIES, every_label=E.every_label, accounts=E.ACCOUNTS, budget_year=budget_year(),
                qbo_words=E.QBO_WORDS, qbo_split=E.qbo_split, about_url=_about_url, gear_views=GEAR_VIEWS, **kw)


def _key(name):
    return ' '.join(str(name or '').lower().split())


def _about_url(about):
    """The page of the gear item or storage place a to-do or bill is about, when its "about" is that thing's name
    ('' for free text). Looked up once a page, and only when a row has an "about" at all."""
    k = _key(about)
    if not k:
        return ''
    names = flask_g.get('equip_about')
    if names is None:
        names = {}
        for i in E.items():
            if i['status'] != 'Gone':
                for n in (i['name'], i.get('nickname')):
                    if _key(n):
                        names.setdefault(_key(n), url_for('equip.item', item_id=i['id']))
        for p in E.places():      # a place wins over an item of the same name: the bill is the place's
            if _key(p['name']):
                names[_key(p['name'])] = url_for('equip.place', place_id=p['id'])
        flask_g.equip_about = names
    return names.get(k, '')


def _about_names():
    """What a to-do's "about" offers: every gear item that is not gone, and every storage place in use."""
    return sorted({i['name'] for i in E.items() if i['status'] != 'Gone' and i['name']} | {p['name'] for p in E.places() if p['name']}, key=str.lower)


def _moved(endpoint, anchor='', **kw):
    """An old address: on to where that page lives now, with whatever the link asked for."""
    args = {k: v for k, v in request.args.items() if k not in kw}
    return redirect(url_for(endpoint, **kw, **args) + anchor)


def budget_year():
    """The Operations budget people are working on: next year's from September (when plans are built), else this year's."""
    today = date.today()
    return today.year + 1 if today.month >= 9 else today.year


def _guard():
    if not E.enabled():
        flash('Operations isn\'t set up on this server yet. Ask admin@drscca.org.', 'warning')
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
            flash('Changes are made by the Operations committee and the club officers.', 'warning')
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


def _qbo_check(f, row, current):
    """The "Checked against QuickBooks" dropdown and note, put together into the row's qbo_check. True when the person
    changed it; left alone, the cell keeps exactly what it had (older free text is not rewritten)."""
    if 'qbo_word' not in f and 'qbo_note' not in f:
        return False
    import json
    try:
        base = json.loads(f.get('_base') or 'null')
    except ValueError:
        base = None
    was = (base if isinstance(base, dict) else current or {}).get('qbo_check', '')
    word, note = E.qbo_split(was)
    new = E.qbo_join(f.get('qbo_word', ''), f.get('qbo_note', ''))
    if new == E.qbo_join(word, note):
        row['qbo_check'] = (current or {}).get('qbo_check', '')
        return False
    row['qbo_check'] = new
    return True


def _diff(before, after):
    """Fields a quick action changed on a row."""
    return {k for k in after if k != 'id' and E.norm(after.get(k)) != E.norm((before or {}).get(k))}


def _who():
    return (session.get('equip_editor') or {}).get('name') or session.get('who') or 'Unknown'


@bp.errorhandler(E.Gone)
def _row_gone(e):
    """A save for a row someone deleted from the Operations sheet since the page was loaded: say so, not a server error."""
    flash('That row is no longer in the Operations sheet (someone deleted it there), so nothing was saved.', 'warning')
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
    board, reserve = E.board_amount(), E.reserve_now()
    rp = E.reserve_plan(items, today, E.plan_settings(), balance=reserve['amount'])
    big = [i for i in items if i['category'] in BIG and i['status'] != 'Planned']
    for i in big:
        i['lc'] = E.lifecycle(i, today)
    replace_soon = sorted([i for i in big if i['lc'] and i['lc'].get('replace') and i['lc']['replace'] <= today.year + 1], key=lambda i: i['lc']['replace'])
    import equip_docs as D
    if request.args.get('fresh'):
        D.refresh()
    gaps, docs_error = _doc_gaps(items, places)
    return render_template('equip.html', **_ctx(view='home', today=t_iso, overdue=overdue, coming=coming,
                                                n_due=sum(1 for t in todo if t['due'] and t['due'] <= soon), undated=[t for t in todo if not t['due'] and not t['last_done']],
                                                doc_gaps=gaps, docs_error=docs_error, root=os.environ.get('OPERATIONS_DOCS_FOLDER_URL', ''),
                                                bills_year=sum(b['per_year'] for b in bills), n_bills=len(bills), rp=rp, board=board, reserve=reserve,
                                                n_vehicles=sum(1 for i in big if i['category'] == 'Vehicle'), n_trailers=sum(1 for i in big if i['category'] == 'Trailer'),
                                                replace_soon=replace_soon))


def _doc_gaps(items, places):
    """Documents that are missing, one entry per vehicle, trailer, place or region-wide row, each with the page whose
    upload form takes it: [{'name', 'missing', 'url'}], and whether Drive could not be read. A vehicle or trailer with
    no Documents row yet is missing everything its kind needs."""
    import equip_docs as D
    rows = D.status(E.documents())
    live = {i['id']: i for i in items}
    open_places = {p['id'] for p in places}
    out = []
    for r in rows:
        rid = r['for_id']
        if not r['missing'] or (rid.startswith('EQ') and rid not in live) or (rid.startswith('PL') and rid not in open_places):
            continue
        url = url_for('equip.item', item_id=rid) + '#add-document' if rid.startswith('EQ') else url_for('equip.place', place_id=rid) + '#add-document' \
            if rid.startswith('PL') else url_for('equip.money') + '#region-documents'
        out.append({'name': r['for_name'], 'missing': r['missing'], 'url': url})
    have_row = {r['for_id'] for r in rows}
    for i in items:
        if i['category'] in BIG and i['status'] != 'Planned' and i['id'] not in have_row:
            out.append({'name': i['name'], 'missing': D.needed({'needed': D.DEFAULT_NEEDED[i['category']]}),
                        'url': url_for('equip.item', item_id=i['id']) + '#add-document'})
    return out, any(r['error'] for r in rows)


def _roll(b, today):
    """An automatic charge's next date on or after today; the sheet's date can be a past one nobody moved on."""
    d = b['next']
    if not d or d >= today:
        return d or ''
    return E.next_due({'due': d, 'repeat_months': b['every']}, after=(date.fromisoformat(today) - timedelta(days=1)).isoformat()) or ''


@bp.route('/vehicles')
@login_required
def vehicles():
    """Vehicles was a tab; it is Gear's "Vehicles and trailers" filter now."""
    return _moved('equip.items', by='vehicles')


def _vehicles(rows):
    """Vehicles and trailers the region has (planned purchases are on Replacement): where each one is kept, its condition, value left and when to replace it."""
    today = date.today()
    big = sorted([i for i in rows if i['status'] not in ('Gone', 'Planned') and i['category'] in BIG], key=lambda i: (i['category'], i['name']))
    import equip_docs as D
    docs = {r['for_id']: r for r in D.status([d for d in E.documents() if d['for_id']])}
    for i in big:
        i['lc'] = E.lifecycle(i, today)
        d = docs.get(i['id'])
        i['docs'] = {'have': len(d['have']), 'missing': d['missing'], 'error': d['error']} if d else \
            {'have': 0, 'missing': D.needed({'needed': D.DEFAULT_NEEDED.get(i['category'], '')}), 'error': ''}
    return big


@bp.route('/items')
@login_required
def items():
    """Gear, one list four ways: everything by name, the vehicles and trailers, by where it is kept, or by category."""
    if (g := _guard()):
        return g
    by = request.args.get('by', 'all')
    if by not in dict(GEAR_VIEWS):
        by = 'all'
    rows = E.items()
    show_gone = request.args.get('gone') == '1'
    n_live = sum(1 for i in rows if i['status'] not in ('Gone', 'Planned'))
    if by == 'vehicles':
        return render_template('equip.html', **_ctx(view='gear', by=by, page_title=PAGE_TITLES['vehicles'], big=_vehicles(rows), n_live=n_live))
    if by == 'place':
        where, people, other, unknown = E.locations(rows, E.places())
        return render_template('equip.html', **_ctx(view='gear', by=by, page_title=PAGE_TITLES['where'], where=where, people=people, other=other,
                                                    unknown=unknown, n_live=n_live))
    rows = [i for i in rows if show_gone or i['status'] != 'Gone']
    if by == 'category':
        groups = [(c, [i for i in rows if i['category'] == c]) for c in E.CATEGORIES] + [('', [i for i in rows if i['category'] not in E.CATEGORIES])]
        return render_template('equip.html', **_ctx(view='gear', by=by, page_title=PAGE_TITLES['items'], n_live=n_live, show_gone=show_gone,
                                                    groups=[(c or 'Uncategorized', g) for c, g in groups if g]))
    return render_template('equip.html', **_ctx(view='gear', by=by, page_title=PAGE_TITLES['all'], n_live=n_live, show_gone=show_gone,
                                                rows=sorted(rows, key=lambda i: i['name'].lower())))


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
    mine = {_key(n) for n in (it.get('name'), it.get('nickname')) if _key(n)} if item_id else set()
    tasks = [t for t in E.tasks() if _key(t['about']) in mine and not _finished(t)]
    return render_template('equip.html', **_ctx(view='item', page_title=it.get('name') or 'New item', it=it, lc=lc, edit=edit, locations=_location_names(), tasks=tasks,
                                                docs=_docs_for(it['id'], it.get('category'), it.get('name', '')) if it and it.get('id') and not edit else None, today=date.today().isoformat()))


def _number(v):
    try:
        return float(E.norm(v))
    except ValueError:
        return None


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
        stamp = f"{'Removed' if gone else 'Put back'} {E.nice_date(date.today().isoformat())} by {_who()}."
        row.update(status='Gone' if gone else 'In use', notes=((row.get('notes') or '') + '\n' + stamp).strip(),
                   updated=date.today().isoformat(), updated_by=_who())
        E.save('Property', row, changed=_diff(it, row))
        log_audit_event('EQUIP_ITEM_REMOVED' if gone else 'EQUIP_ITEM_RESTORED', _who(), {'id': it['id'], 'name': it['name']})
        flash(f"{it['name']} {'removed. It stays on record as Gone' if gone else 'is back in use'}.", 'success')
        return redirect(local_path(f.get('back')) or url_for('equip.item', item_id=it['id']))
    if f.get('action') in ('replacement', 'off_plan'):
        # The quick edit on Replacement and on the item's own page: just the year and the cost estimate. "Take off
        # the plan" clears both; the item itself stays in Gear.
        off = f['action'] == 'off_plan'
        back = local_path(f.get('back')) or url_for('equip.replacement') + f"#{it['id']}"
        for k in ('replace_year', 'replace_cost'):
            row[k] = '' if off else f.get(k, '').replace('$', '').replace(',', '').strip()
        if not off and any(row[k] and _number(row[k]) is None for k in ('replace_year', 'replace_cost')):
            flash('Enter the year and the cost as numbers.', 'danger')
            return redirect(back)
        row.update(updated=date.today().isoformat(), updated_by=_who())
        E.save('Property', row, changed=_diff(it, row))
        log_audit_event('EQUIP_ITEM_SAVED', _who(), {'id': it['id'], 'name': it['name'], 'replace_year': row['replace_year'], 'replace_cost': row['replace_cost']})
        if not off:
            flash(f"{it['name']}: replacement updated.", 'success')
            return redirect(back)
        lc = E.lifecycle(dict(it, replace_year=None, replace_cost=None))
        if lc and it.get('cost') and it['status'] != 'Gone':
            flash(f"{it['name']}: replacement year and cost cleared. It is still on the plan for {lc['replace']}, from its year bought and useful life. "
                  'It is still in Gear; open it there to change those.', 'warning')
        else:
            flash(f"{it['name']} is off the replacement plan. It is still in Gear.", 'success')
        return redirect(local_path(f.get('back')) or url_for('equip.replacement'))
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
    """Storage places are the sections of Gear's "By place" list."""
    return _moved('equip.items', by='place')


@bp.route('/documents')
@login_required
def documents():
    """Documents was a tab. What is missing is on Overview now, each thing's documents are on its own page, and the
    region-wide ones (insurance) are on Money."""
    return _moved('equip.home', anchor='#documents')


def _region_docs():
    """The Documents rows that belong to no item or place ("Insurance and region"), with what is there and missing."""
    import equip_docs as D
    rows = D.status([d for d in E.documents() if not d['for_id']])
    for r in rows:
        r['kinds'] = D.kinds_for(r)
    return rows


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
    rid = f.get('for_id', '')
    back = local_path(f.get('back')) or (url_for('equip.item', item_id=rid) + '#documents' if rid.startswith('EQ') else url_for('equip.place', place_id=rid) + '#documents'
                                         if rid.startswith('PL') else url_for('equip.money') + '#region-documents')
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
            flash('No Operations folder is set up in Drive yet. Ask admin@drscca.org.', 'warning')
            return redirect(back)
        name = it['name'] if it else p['name']
        folder = drive_folder(name, D.section_folder(root, rid))
        row = {'for_id': rid, 'for_name': name, 'folder': folder['webViewLink'],
               'needed': D.DEFAULT_NEEDED.get(it['category'] if it else 'place', ''), 'notes': ''}
        row['id'] = E.save('Documents', row)
        log_audit_event('EQUIP_DOCS_FOLDER_MADE', _who(), {'id': row['id'], 'for': rid, 'name': name})
    folder = D.folder_id(row['folder'])
    if not folder:
        flash(f"{row['for_name']} has no Drive folder link on the Documents tab.", 'warning')
        return redirect(back)
    names, failed = [], []
    for n, x in enumerate(files, 1):
        name = D.file_name(doc, row['for_name'], f.get('note', ''), x.filename, n if len(files) > 1 else 1)
        try:
            D.upload(folder, name, x, _who())
            names.append(name)
        except Exception:          # say which files made it: the ones before this one are already in Drive
            import logging
            logging.exception(f'Operations document upload failed for {x.filename}')
            failed.append(x.filename)
    D.refresh()
    if names:
        log_audit_event('EQUIP_DOCS_UPLOADED', _who(), {'id': row['id'], 'for': row['for_name'], 'doc': doc, 'files': names})
    if failed:
        flash((f"Saved {len(names)} of {len(files)} files for {row['for_name']}. " if names else 'Google Drive did not answer, so nothing was saved. ')
              + f"Not saved: {', '.join(failed)}. Add {'just those' if names else 'them'} again.", 'warning')
    else:
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
    about = [t for t in E.tasks() if place_id and _key(p.get('name')) and _key(t['about']) == _key(p['name'])]
    bills = [b for b in E.recurring_costs([p] if place_id and p.get('status') != 'Closed' else [], about)]
    for b in bills:
        b['how_often'] = E.every_label(b['every'])
    paid = {b['id'] for b in bills}
    return render_template('equip.html', **_ctx(view='place', page_title=p.get('name') or 'New place', p=p, edit=edit, held=held, back=local_path(back),
                                                bills=bills, tasks=[t for t in about if t['id'] not in paid and not E.is_auto(t) and not _finished(t)],
                                                today=date.today().isoformat(),
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
        return redirect(url_for('equip.items', by='place') if f['action'] == 'close' else url_for('equip.place', place_id=p['id']))
    for k in E.PLACE_COLS:
        if k in f:
            row[k] = f.get(k, '').strip()
    if not row.get('name'):
        flash('Give the place a name.', 'danger')
        return redirect(request.url)
    row.update(updated=date.today().isoformat(), updated_by=_who())
    qbo = {'qbo_check'} if _qbo_check(f, row, p) else set()
    pid = E.save('Storage', row, changed=None if not row.get('id') else _changed(f, E.PLACE_COLS, p) | {'updated', 'updated_by'} | qbo)
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
              ('No date yet', [t for t in rows if not t['due'] and not _finished(t)]),
              ('Done', sorted([t for t in rows if _finished(t)], key=lambda t: t['last_done'], reverse=True))]
    return render_template('equip.html', **_ctx(view='tasks', groups=[(n, g) for n, g in groups if g], today=today.isoformat()))


def _finished(t):
    """A one-time to-do that was marked done: Done clears its date, so it must not sit under "No date yet"."""
    return not t['due'] and bool(t['last_done']) and not t['repeat_months']


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
    if not task_id and request.args.get('about'):
        t = {'about': request.args['about']}      # "Add a to-do" on an item's or place's page starts with its name
    names = _about_names()
    back = request.args.get('back', '')
    bill = request.args.get('bill') == '1' or bool(t.get('cost'))
    return render_template('equip.html', **_ctx(view='task', page_title=t.get('task') or ('New bill' if bill else 'New to-do'), t=t, names=names,
                                                frequencies=E.FREQUENCIES, back=local_path(back), bill=bill))


@editor_required
def _save_task(t):
    f = request.form
    row = dict(t or {})
    if f.get('action') == 'done':
        # Done: note who and when, and move a repeating task to its next date after today, however overdue it was (a
        # one-time task stays, marked done). was_due: the date the page showed; if the sheet has another by now, a
        # second click or someone else already marked it, and it is left alone (not moved on a second cycle).
        today = date.today().isoformat()
        back = local_path(f.get('back', '')) or url_for('equip.tasks')
        was = f.get('was_due')
        if (was is not None and was != t['due']) or (was is None and t['last_done'] == today):
            flash(f"{t['task']} was already marked done. Nothing was changed.", 'info')
            return redirect(back)
        row.update(last_done=today, done_by=_who())
        nxt = E.next_due(t, after=today)
        row['due'] = nxt
        try:
            E.save('Recurring costs and to-dos', row, changed=_diff(t, row), expect={'due': t['due'], 'last_done': t['last_done']})
        except E.Changed:
            flash(f"{t['task']} was already marked done. Nothing was changed.", 'info')
            return redirect(back)
        log_audit_event('EQUIP_TASK_DONE', _who(), {'id': t['id'], 'task': t['task'], 'next': nxt})
        flash(f"Marked done: {t['task']}." + (f" Next due {E.nice_date(nxt)}." if nxt else ''), 'success')
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
    if _qbo_check(f, row, t):
        ch.add('qbo_check')
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


@bp.route('/money')
@login_required
def money():
    """Money, first part: every bill the region pays on a schedule (storage, insurance, subscriptions, registrations),
    by category, and the insurance and region-wide documents."""
    if (g := _guard()):
        return g
    if request.args.get('fresh'):
        import equip_docs as D
        D.refresh()
    rows = E.recurring_costs(E.places(), E.tasks())
    for r in rows:
        r['how_often'] = E.every_label(r['every'])
    cats = {c: [] for c in E.COST_CATEGORIES}
    for r in rows:      # a category typed by hand in the sheet still counts: under Other
        cats[r['category'] if r['category'] in cats else 'Other'].append(r)
    return render_template('equip.html', **_ctx(view='money', part='bills', page_title=PAGE_TITLES['costs'], cats=[(c, r) for c, r in cats.items() if r],
                                                total=sum(r['per_year'] for r in rows), region_docs=_region_docs(),
                                                today=date.today().isoformat(), plan_year=date.today().year + 1))


@bp.route('/bills')
@login_required
def costs():
    """Bills was a tab; it is the first part of Money now."""
    return _moved('equip.money')


@bp.route('/costs')
@login_required
def old_costs():
    return _moved('equip.money')


@bp.route('/money/replacement')
@login_required
def replacement():
    """Money, second part: when each long-lived item reaches the end of its life, what the plan suggests setting aside,
    what the board set aside, and what is in the reserve."""
    if (g := _guard()):
        return g
    rows = [i for i in E.items() if i['status'] != 'Gone']
    board, reserve = E.board_amount(), E.reserve_now()
    rp = E.reserve_plan(rows, cfg=E.plan_settings(), balance=reserve['amount'])
    missing = [i for i in rows if E.is_capital(i) and not E.lifecycle(i) and i['status'] != 'Retire']
    by_year = {}
    for r in rp['near']:
        by_year[r['year']] = by_year.get(r['year'], 0) + r['cost']
    proj = E.reserve_projection(rp['later'], rp['start'], board['monthly'] if board else None, reserve['amount'])
    return render_template('equip.html', **_ctx(view='money', part='replacement', page_title=PAGE_TITLES['plan'], rp=rp, by_year=by_year, missing=missing,
                                                note=E.plan_note(), note_max=E.NOTE_MAX, board=board, reserve=reserve,
                                                projection=E.projection_text(proj), changed=E.plan_changed(rp), officer=bool(officer_current()),
                                                reserve_account=E.RESERVE_ACCOUNT, today=date.today().isoformat()))


@bp.route('/plan')
@login_required
def plan():
    """Replacement was a tab; it is the second part of Money now."""
    return _moved('equip.replacement')


@bp.route('/plan/settings', methods=['POST'])
@login_required
@editor_required
def plan_settings():
    """Save the replacement plan's one rule: how soon a purchase is "buy soon" and goes in that year's budget. Anything
    else a form sends (the old big-item fields) is ignored."""
    if (g := _guard()):
        return g
    f, old = request.form, E.plan_settings()
    back = url_for('equip.replacement')
    new = dict(old)
    try:
        if 'budget_within_years' in f:
            new['budget_within_years'] = int(float(E.norm(f['budget_within_years'])))
    except ValueError:
        flash('Enter a number of years.', 'danger')
        return redirect(back)
    if not 0 <= new['budget_within_years'] <= 5:
        flash('Enter 0 to 5 years.', 'danger')
        return redirect(back)
    if new == old:
        flash('Nothing changed.', 'info')
        return redirect(back)
    E.save_plan_settings(new, _who(), date.today().isoformat())
    log_audit_event('EQUIP_PLAN_SETTINGS', _who(), {'before': old, 'after': new})
    flash('Saved. The numbers below use the new rule.', 'success')
    return redirect(back)


@bp.route('/plan/board', methods=['POST'])
@login_required
def plan_board():
    """Record what the board set aside each month for replacements, and the date it decided. Only for people whose
    role may record board decisions (the officers). Nothing else on the plan is touched, and nothing goes to QuickBooks."""
    back = url_for('equip.replacement')
    me = officer_current()
    if not me:
        from auth import refuse
        return refuse("Recording the board's amount is for the roles the admin allows it.", back)
    if (g := _guard()):
        return g
    f, old = request.form, E.board_amount()
    try:
        monthly = round(float(E.norm(f.get('board_monthly', ''))), 2)
        decided = date.fromisoformat(f.get('board_date', '').strip()).isoformat()
    except ValueError:
        flash('Enter the monthly amount as a number and the date the board decided. Nothing was saved.', 'danger')
        return redirect(back)
    if not 0 <= monthly <= 1000000 or decided > date.today().isoformat():
        flash("The amount can't be negative and the date can't be in the future. Nothing was saved.", 'danger')
        return redirect(back)
    if old and (old['monthly'], old['date']) == (monthly, decided):
        flash('Nothing changed.', 'info')
        return redirect(back)
    E.save_board_amount(monthly, decided, me['name'], date.today().isoformat())
    log_audit_event('EQUIP_PLAN_BOARD', me['name'], {'before': old or {}, 'after': {'monthly': monthly, 'date': decided, 'by': me['name']}})
    flash(f"Recorded: the board set aside ${monthly:,.0f} a month.", 'success')
    return redirect(back)


@bp.route('/plan/note', methods=['POST'])
@login_required
@editor_required
def plan_note():
    """Save (or, left blank, clear) the committee's short note under the replacement summary. Nothing else is touched."""
    if (g := _guard()):
        return g
    back = url_for('equip.replacement')
    text, old = ' '.join(request.form.get('note', '').split()), E.plan_note()
    if len(text) > E.NOTE_MAX:
        flash(f'Keep the note to {E.NOTE_MAX} characters. Nothing was saved.', 'danger')
        return redirect(back)
    if text == old:
        flash('Nothing changed.', 'info')
        return redirect(back)
    E.save_plan_note(text, _who(), date.today().isoformat())
    log_audit_event('EQUIP_PLAN_NOTE', _who(), {'before': old, 'after': text})
    flash('Note saved.' if text else 'Note removed.', 'success')
    return redirect(back)


def overdue_count():
    """For the Treasurer page."""
    today = date.today().isoformat()
    return sum(1 for t in E.tasks() if t['due'] and t['due'] < today and not E.is_auto(t)) if E.enabled() else 0
