"""Hassles: anyone signed in (officials and volunteers) posts something that is a hassle, in the apps or in how the
region does things. Everyone sees them all, gives a thumbs up to the ones that bother them too, and can reply. Whoever
has the hassles permission (the officers by default) marks each Open, Planned, Fixed or Not doing, with a short note. admin@ is emailed about each new hassle and
each reply. Stored in the Registry sheet (hassles_store)."""
import os

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import auth
import hassles_store as S
from utils import log_audit_event, login_required, send_mail

bp = Blueprint('hassles', __name__, url_prefix='/hassles')
STATUSES = S.STATUSES
CLOSED = ('Fixed', 'Not doing')
ADMIN = os.environ.get('HASSLES_EMAIL', 'admin@drscca.org')      # told about each new hassle and reply


def _me():
    u = auth.user() or {}
    return (u.get('email') or '').lower(), u.get('name') or u.get('email') or 'Someone', 'hassles' in set(u.get('perms') or ())


def _clean(text, n):
    return ' '.join((text or '').split())[:n]


def _guard():
    if not S.enabled():
        flash("Hassles isn't set up on this server yet. Ask admin@drscca.org.", 'warning')
        return redirect(url_for('home'))
    return None


def _back(hid=None):
    return redirect(url_for('hassles.home') + (f'#s{hid}' if hid else ''))


def _tell_admin(subject, body, reply_to):
    try:
        send_mail(subject, body, ADMIN, reply_to=reply_to or None)
    except Exception:          # the post is saved either way; a mail problem must not lose it
        import logging
        logging.exception('Hassles: could not email the admin')


@bp.route('/')
@login_required
def home():
    if (g := _guard()):
        return g
    email, _, is_admin = _me()
    items = S.hassles()
    # Open ones first, most thumbs up first, then newest; finished ones at the bottom.
    items.sort(key=lambda s: (s['status'] in CLOSED, -len(s['votes']), -s['id']))
    return render_template('hassles.html', title='DR Apps hassles', page_title='Hassles', items=items,
                           me=email, is_admin=is_admin, statuses=STATUSES, closed=CLOSED)


@bp.route('/new', methods=['POST'])
@login_required
def new():
    if (g := _guard()):
        return g
    email, name, _ = _me()
    title = _clean(request.form.get('title'), 120)
    details = (request.form.get('details') or '').strip()[:2000]
    if not title:
        flash('Say in a few words what the hassle is.', 'warning')
        return _back()
    hid = S.add(title, details, name, email)
    log_audit_event('HASSLE_ADDED', name, {'id': hid, 'title': title})
    _tell_admin(f'New hassle: {title}', f"{name} posted a hassle in DR Apps:\n\n{title}\n\n" + (f"{details}\n\n" if details else '')
                + url_for('hassles.home', _external=True) + f'#s{hid}', email)
    flash('Thanks! It is on the list.', 'success')
    return _back(hid)


@bp.route('/<int:sid>/vote', methods=['POST'])
@login_required
def vote(sid):
    """Thumbs up, or take it back. Not on your own, and not once it is fixed or closed."""
    if (g := _guard()):
        return g
    email, _, _ = _me()
    h = S.get(sid)
    if not h:
        abort(404)
    if h['by_email'] != email and h['status'] not in CLOSED:
        S.toggle_vote(sid, email)
    return _back(sid)


@bp.route('/<int:sid>/reply', methods=['POST'])
@login_required
def reply(sid):
    """Anyone signed in can reply."""
    if (g := _guard()):
        return g
    email, name, is_admin = _me()
    text = (request.form.get('text') or '').strip()[:2000]
    h = S.get(sid)
    if not h:
        abort(404)
    if not text:
        flash('Write a reply first.', 'warning')
        return _back(sid)
    S.reply(sid, text, name, email)
    log_audit_event('HASSLE_REPLY', name, {'id': sid})
    if not is_admin:
        _tell_admin(f"Reply on hassle: {h['title']}", f"{name} replied:\n\n{text}\n\n"
                    + url_for('hassles.home', _external=True) + f'#s{sid}', email)
    return _back(sid)


@bp.route('/<int:sid>/reply/delete', methods=['POST'])
@login_required
def delete_reply(sid):
    """Whoever wrote the reply, or someone with the hassles permission."""
    if (g := _guard()):
        return g
    email, name, is_admin = _me()
    at, author = request.form.get('at', ''), (request.form.get('by_email') or '').lower()
    if not (is_admin or author == email):
        abort(403)
    S.delete_reply(sid, at, author)
    log_audit_event('HASSLE_REPLY_DELETED', name, {'id': sid, 'at': at})
    return _back(sid)


@bp.route('/<int:sid>/status', methods=['POST'])
@login_required
def status(sid):
    if (g := _guard()):
        return g
    _, name, is_admin = _me()
    if not is_admin:
        return auth.refuse("Your role can't mark hassles planned or fixed. Ask the admin if you need it.", url_for('hassles.home'))
    st = request.form.get('status')
    if st not in STATUSES:
        abort(400)
    note = _clean(request.form.get('note'), 300)
    if not S.update(sid, status=st, note=note):
        abort(404)
    log_audit_event('HASSLE_STATUS', name, {'id': sid, 'status': st, 'note': note})
    return _back(sid)


@bp.route('/<int:sid>/delete', methods=['POST'])
@login_required
def delete(sid):
    """Whoever posted it, or someone with the hassles permission."""
    if (g := _guard()):
        return g
    email, name, is_admin = _me()
    h = S.get(sid)
    if not h:
        return _back()
    if not (is_admin or h['by_email'] == email):
        abort(403)
    S.delete(sid)
    log_audit_event('HASSLE_DELETED', name, {'id': sid, 'title': h['title']})
    flash('Deleted.', 'info')
    return _back()
