"""Officer sign-in: the few things that stand for a board decision (recording that the board approved an event budget)
need more than the shared app password and a typed name. The RE, ARE and treasurer sign in through a link emailed to
their address in the read-only officials list; the link sets an officer session on that device for 30 days.
"""
from functools import wraps

from flask import Blueprint, current_app, flash, redirect, request, session, url_for
from itsdangerous import URLSafeTimedSerializer

import officials as O
from utils import local_path

bp = Blueprint('officer', __name__, url_prefix='/officer')

ROLES = ('RE', 'ARE', 'Secretary', 'Treasurer', 'Admin')   # officers per bylaws Art. IV 4.2, plus the admin account
LINK_DAYS = 30


def officers(perm='board'):
    """{email: name} of the officials whose role has this permission (Admin > Roles), from the officials list only."""
    import access
    return {o['email'].lower(): o['name'] for o in O.all() if perm in access.perms(o.get('role', '')) and o.get('email')}


def current(perm='board'):
    """The signed-in person {'email', 'name'} when one of their roles has this permission, or None. An API key acts
    as an officer with every permission."""
    from flask import g
    if getattr(g, 'api_user', None):
        return session.get('officer')
    from auth import user
    u = user()
    return {'email': u['email'], 'name': u['name']} if u and perm in (u.get('perms') or ()) else None


MESSAGES = {'board': 'Recording a board decision', 'pay': 'Marking payments'}


def officer_required(f=None, perm='board'):
    """@officer_required (board decisions) or @officer_required(perm='pay')."""
    if f is None:
        return lambda fn: officer_required(fn, perm)

    @wraps(f)
    def wrapped(*a, **kw):
        if not current(perm):
            flash(f"{MESSAGES.get(perm, 'That')} is for the roles the admin allows it.", 'warning')
            back = request.form.get('back') or request.referrer or ''
            if back.startswith(request.host_url):
                back = '/' + back[len(request.host_url):]
            return redirect(local_path(back) or url_for('home'))
        return f(*a, **kw)
    return wrapped


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='officer-signin')


@bp.route('/signin', methods=['GET', 'POST'])
@bp.route('/signin/<token>')
def signin(token=None):
    """Old separate sign-in: everyone now signs in once by email code, and the role comes from the officials list."""
    flash('This needs an officer (RE, ARE, secretary or treasurer), signed in with their own email address.', 'warning')
    return redirect(url_for('home'))


@bp.route('/signout', methods=['POST'])
def signout():
    session.pop('officer', None)
    return redirect(request.referrer or url_for('home'))
