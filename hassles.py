"""Hassles: anyone signed in (officials and volunteers) posts something that is a hassle, in the apps or in how the
region does things; everyone sees them all and gives a thumbs up to the ones that bother them too. The admin marks each one Planned, Done or Not doing, with a short note.

Kept in data/suggestions.json as {'next': n, 'items': [...]}; each item has id, title, details, by (name), by_email,
created, votes (emails), status and note.
"""
import json
import os
from datetime import date

from filelock import FileLock
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import auth
from storage import DATA_DIR
from utils import log_audit_event, login_required

bp = Blueprint('hassles', __name__, url_prefix='/hassles')
PATH = os.path.join(DATA_DIR, 'suggestions.json')
STATUSES = ['Open', 'Planned', 'Fixed', 'Not doing']
CLOSED = ('Fixed', 'Not doing')


def _load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'next': 1, 'items': []}


def _write(doc):
    tmp = f'{PATH}.{os.getpid()}.tmp'
    with open(tmp, 'w') as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, PATH)


def _change(fn):
    """Read, change and write as one step (the app runs several workers)."""
    with FileLock(PATH + '.lock', timeout=10):
        doc = _load()
        out = fn(doc)
        _write(doc)
        return out


def _me():
    u = auth.user() or {}
    return (u.get('email') or '').lower(), u.get('name') or u.get('email') or 'Someone', 'admin' in set(u.get('perms') or ())


def _find(doc, sid):
    return next((s for s in doc['items'] if s['id'] == sid), None)


def _clean(text, n):
    return ' '.join((text or '').split())[:n]


@bp.route('/')
@login_required
def home():
    email, _, is_admin = _me()
    items = _load()['items']
    # Open ideas first, most thumbs up first, then newest; finished ones at the bottom.
    items.sort(key=lambda s: (s['status'] in CLOSED, -len(s['votes']), -s['id']))
    return render_template('hassles.html', title='DR Apps hassles', page_title='Hassles', items=items,
                           me=email, is_admin=is_admin, statuses=STATUSES, closed=CLOSED)


@bp.route('/new', methods=['POST'])
@login_required
def new():
    email, name, _ = _me()
    title = _clean(request.form.get('title'), 120)
    details = (request.form.get('details') or '').strip()[:2000]
    if not title:
        flash('Say in a few words what the hassle is.', 'warning')
        return redirect(url_for('hassles.home'))

    def add(doc):
        sid = doc['next']
        doc['next'] = sid + 1
        doc['items'].append({'id': sid, 'title': title, 'details': details, 'by': name, 'by_email': email,
                             'created': date.today().isoformat(), 'votes': [], 'status': 'Open', 'note': ''})
        return sid
    sid = _change(add)
    log_audit_event('SUGGESTION_ADDED', name, {'id': sid, 'title': title})
    flash('Thanks! It is on the list.', 'success')
    return redirect(url_for('hassles.home') + f'#s{sid}')


@bp.route('/<int:sid>/vote', methods=['POST'])
@login_required
def vote(sid):
    """Thumbs up, or take it back. Not on your own suggestion."""
    email, _, _ = _me()

    def toggle(doc):
        s = _find(doc, sid)
        if not s:
            abort(404)
        if s['by_email'] == email or s['status'] in CLOSED:
            return
        if email in s['votes']:
            s['votes'].remove(email)
        else:
            s['votes'].append(email)
    _change(toggle)
    return redirect(url_for('hassles.home') + f'#s{sid}')


@bp.route('/<int:sid>/status', methods=['POST'])
@login_required
def status(sid):
    email, name, is_admin = _me()
    if not is_admin:
        return auth.refuse('Only the admin marks hassles planned or fixed.', url_for('hassles.home'))
    st = request.form.get('status')
    if st not in STATUSES:
        abort(400)
    note = _clean(request.form.get('note'), 300)

    def mark(doc):
        s = _find(doc, sid)
        if not s:
            abort(404)
        s['status'], s['note'] = st, note
    _change(mark)
    log_audit_event('SUGGESTION_STATUS', name, {'id': sid, 'status': st, 'note': note})
    return redirect(url_for('hassles.home') + f'#s{sid}')


@bp.route('/<int:sid>/delete', methods=['POST'])
@login_required
def delete(sid):
    """Whoever posted it, or the admin."""
    email, name, is_admin = _me()

    def drop(doc):
        s = _find(doc, sid)
        if not s:
            return None
        if not (is_admin or s['by_email'] == email):
            abort(403)
        doc['items'].remove(s)
        return s['title']
    title = _change(drop)
    if title:
        log_audit_event('SUGGESTION_DELETED', name, {'id': sid, 'title': title})
        flash('Deleted.', 'info')
    return redirect(url_for('hassles.home'))
