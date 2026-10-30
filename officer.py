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
            flash('Recording a board decision needs a sign-in link sent to the RE, ARE or treasurer.', 'warning')
            back = request.form.get('back') or request.referrer or request.full_path.rstrip('?')
            return redirect(url_for('officer.signin', next=back if back.startswith('/') or back.startswith(request.host_url) else ''))
        return f(*a, **kw)
    return wrapped


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='officer-signin')


@bp.route('/signin', methods=['GET', 'POST'])
@login_required
def signin():
    if request.method == 'POST':
        mail = request.form.get('email', '').strip().lower()
        nxt = request.form.get('next', '')
        if mail in officers():
            token = _serializer().dumps({'email': mail, 'name': officers()[mail], 'next': nxt})
            link = f"{request.url_root.rstrip('/')}{url_for('officer.signin_link', token=token)}"
            send_mail('Your officer sign-in link for DRSCCA', f"Use this link to record board decisions in the DRSCCA apps (good for {LINK_DAYS} days, "
                      f"on the device you open it on):\n\n{link}\n\nIf you did not ask for it, ignore this email.", mail)
        flash('If that address belongs to the RE, ARE or treasurer, a sign-in link is on its way.', 'info')
        return redirect(nxt if nxt.startswith('/') else url_for('home'))
    nxt = request.args.get('next', '')
    if nxt.startswith(request.host_url):
        nxt = '/' + nxt[len(request.host_url):]
    return render_template('officer.html', nxt=nxt if nxt.startswith('/') else '', title='Officer sign-in')


@bp.route('/signin/<token>')
@login_required
def signin_link(token):
    try:
        t = _serializer().loads(token, max_age=LINK_DAYS * 86400)
    except BadSignature:
        flash('That sign-in link has expired or is not valid. Ask for a new one.', 'warning')
        return redirect(url_for('officer.signin'))
    if t['email'] not in officers():
        abort(403)
    session['officer'] = {'email': t['email'], 'name': t['name']}
    log_audit_event('OFFICER_SIGNIN', t['name'], {'email': t['email']})
    flash(f"Signed in as {t['name']} to record board decisions.", 'success')
    nxt = t.get('next') or ''
    return redirect(nxt if nxt.startswith('/') else url_for('home'))


@bp.route('/signout', methods=['POST'])
def signout():
    session.pop('officer', None)
    return redirect(request.referrer or url_for('home'))
