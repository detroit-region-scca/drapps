"""Hassles: anyone with the hassles_post permission (every role, volunteers too, by default) posts something that is a hassle, in the apps or in how the
region does things. Everyone sees them all, gives a thumbs up to the ones that bother them too, and can reply. Whoever
has the hassles permission (the officers by default) marks each Open, Planned, Fixed or Not doing, with a short note. admin@ is emailed about each new hassle and
each reply. Anyone signed in can opt in to hassles@drscca.org, a Google Group that gets every new hassle and reply:
the Subscribe button queues their own address (data/hassles-subscribe.json) and the server adds it to that one group
(tools/hassles_subscribe.py, the only place the apps change a Google Group). Stored in the Registry sheet
(hassles_store)."""
import json
import os
from datetime import datetime

from filelock import FileLock
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import auth
import hassles_store as S
import utils
from utils import log_audit_event, login_required, send_mail

bp = Blueprint('hassles', __name__, url_prefix='/hassles')
STATUSES = S.STATUSES
CLOSED = ('Fixed', 'Not doing')
ADMIN = os.environ.get('HASSLES_EMAIL', 'admin@drscca.org')      # told about each new hassle and reply
GROUP = 'hassles@drscca.org'          # people who opted in; fixed here and in tools/hassles_subscribe.py
SUBSCRIBE_QUEUE = os.path.join(utils.DATA_DIR, 'hassles-subscribe.json')
GROUPS_FILE = os.path.join(utils.DATA_DIR, 'email-groups.json')


def _read(path, empty):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return empty


def subscription(email):
    """'on', 'off', 'adding' or 'removing' for this address on hassles@ (a request still waiting wins)."""
    email = (email or '').lower()
    waiting = [c for c in _read(SUBSCRIBE_QUEUE, {}).get('changes', []) if c.get('email') == email]
    if waiting:
        return 'adding' if waiting[-1]['action'] == 'add' else 'removing'
    for g in _read(GROUPS_FILE, {}).get('groups', []):
        if g.get('email') == GROUP:
            return 'on' if any(m.get('email') == email for m in g.get('members', [])) else 'off'
    return 'off'


def _queue(email, action):
    with FileLock(SUBSCRIBE_QUEUE + '.lock'):
        q = _read(SUBSCRIBE_QUEUE, {}) or {}
        q.setdefault('changes', []).append({'email': email.lower(), 'action': action,
                                            'at': datetime.now().astimezone().isoformat(timespec='seconds')})
        tmp = SUBSCRIBE_QUEUE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(q, f, indent=1)
        os.replace(tmp, SUBSCRIBE_QUEUE)


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


def _tell(subject, body, reply_to, admin=True):
    """admin@ (unless an admin wrote it) and everyone who subscribed, through hassles@."""
    try:
        send_mail(subject, body + '\n\nYou get these because you subscribed on the Hassles page. Unsubscribe there.',
                  GROUP, cc=ADMIN if admin else None, reply_to=reply_to or None)
    except Exception:          # the post is saved either way; a mail problem must not lose it
        import logging
        logging.exception('Hassles: could not send the email')


@bp.route('/')
@login_required
def home():
    if (g := _guard()):
        return g
    email, _, is_admin = _me()
    can_post = 'hassles_post' in set((auth.user() or {}).get('perms') or ())
    items = S.hassles()
    # Open ones first, most thumbs up first, then newest; finished ones at the bottom.
    items.sort(key=lambda s: (s['status'] in CLOSED, -len(s['votes']), -s['id']))
    return render_template('hassles.html', title='DR Apps hassles', page_title='Hassles', items=items,
                           me=email, is_admin=is_admin, can_post=can_post, statuses=STATUSES, closed=CLOSED,
                           subscribed=subscription(email), group=GROUP)


@bp.route('/subscribe', methods=['POST'])
@login_required
def subscribe():
    """Opt in or out of hassles@. Only ever the signed-in person's own address, whatever the form sends."""
    if (g := _guard()):
        return g
    email, name, _ = _me()
    action = 'add' if request.form.get('action') == 'add' else 'remove'
    if email and '@' in email:
        _queue(email, action)
        log_audit_event('HASSLES_SUBSCRIBED' if action == 'add' else 'HASSLES_UNSUBSCRIBED', name, {'email': email})
        flash(f"You'll get an email for every new hassle and reply, at {email}." if action == 'add'
              else 'You will no longer get emails about hassles.', 'success')
    return _back()


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
    _tell(f'New hassle: {title}', f"{name} posted a hassle in DR Apps:\n\n{title}\n\n" + (f"{details}\n\n" if details else '')
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
    _tell(f"Reply on hassle: {h['title']}", f"{name} replied:\n\n{text}\n\n"
          + url_for('hassles.home', _external=True) + f'#s{sid}', email, admin=not is_admin)
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
