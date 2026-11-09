"""The checks for things that stand for a decision of the region: recording a board approval, marking a payment.

Everyone signs in once, by emailed code or Google (auth.py); whether they may do these comes from the permissions of
the roles they hold (Admin > Roles), looked up again on every request. An API key is its owner, no more.
"""
from functools import wraps

from flask import Blueprint, flash, redirect, request, url_for

from utils import local_path

bp = Blueprint('officer', __name__, url_prefix='/officer')


def current(perm='board'):
    """The signed-in person {'email', 'name'} when one of their roles has this permission, or None."""
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
            from auth import refuse
            back = request.form.get('back') or request.referrer or ''
            if back.startswith(request.host_url):
                back = '/' + back[len(request.host_url):]
            return refuse(f"{MESSAGES.get(perm, 'That')} is for the roles the admin allows it.", local_path(back) or url_for('home'))
        return f(*a, **kw)
    return wrapped


@bp.route('/signin', methods=['GET', 'POST'])
@bp.route('/signin/<token>')
def signin(token=None):
    """Old separate sign-in links: everyone now signs in once, and what they may do comes from their roles."""
    flash('That link is no longer used. Sign in with your own email address; what you can do comes from your role.', 'warning')
    return redirect(url_for('home'))
