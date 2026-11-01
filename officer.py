"""Officer sign-in: the few things that stand for a board decision (recording that the board approved an event budget)
need more than the shared app password and a typed name. The RE, ARE and treasurer sign in through a link emailed to
their address in the read-only officials list; the link sets an officer session on that device for 30 days.
"""
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

import officials as O
from utils import log_audit_event, login_required, send_mail

bp = Blueprint('officer', __name__, url_prefix='/officer')

ROLES = ('RE', 'ARE', 'Treasurer')
LINK_DAYS = 30


def officers():
    """{email: name} of the officers who can record board decisions, from the officials list only."""
    return {o['email'].lower(): o['name'] for o in O.all() if o.get('role') in ROLES and o.get('email')}


def current():
    """The signed-in officer {'email', 'name'}, or None."""
    o = session.get('officer')
    return o if o and o.get('email', '').lower() in officers() else None


def officer_required(f):
    @wraps(f)
    def wrapped(*a, **kw):
        if not current():
            session.pop('officer', None)
            flash('Recording a board decision is for the RE, ARE or treasurer.', 'warning')
            back = request.form.get('back') or request.referrer or ''
            return redirect(back if back.startswith('/') or back.startswith(request.host_url) else url_for('home'))
        return f(*a, **kw)
    return wrapped


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='officer-signin')


@bp.route('/signin', methods=['GET', 'POST'])
@bp.route('/signin/<token>')
def signin(token=None):
    """Old separate sign-in: everyone now signs in once by email code, and the role comes from the officials list."""
    flash('This needs the RE, ARE or treasurer, signed in with their own email address.', 'warning')
    return redirect(url_for('home'))


@bp.route('/signout', methods=['POST'])
def signout():
    session.pop('officer', None)
    return redirect(request.referrer or url_for('home'))
