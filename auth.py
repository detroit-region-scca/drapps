"""Sign-in by emailed code, and who may do what.

A person types their email address, gets a 6-digit code (and a one-tap link) by email, and is signed in on that
device for 30 days. There is no shared password. What they can do follows from who they are, looked up again on
every request, so removing someone from a list takes effect at once:

  officer    RE, ARE or Treasurer in the read-only officials list: everything, including board decisions.
  official   anyone else in the officials list with an email (directors, directors at large, committee chairs):
             the whole app, as with the old shared password; Operations changes only for the Equipment role.
  volunteer  an address on the Volunteers tab of the Registry sheet (kept by officers on the Access page):
             sees the event schedule, files funds requests and invoices, and sees only their own.

Anyone else gets the same "if that address can use the apps, a code is on its way" answer and no code, so the page
never reveals who is on a list.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import date

from filelock import FileLock
from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for)

import officials as O
from storage import DATA_DIR, store
from utils import log_audit_event, send_mail

bp = Blueprint('auth', __name__)

OFFICER_ROLES = ('RE', 'ARE', 'Secretary', 'Treasurer', 'Admin')   # bylaws Art. IV 4.2, plus the region's admin account
CODE_MINUTES = 15
MAX_TRIES = 5            # wrong codes before that code is dead
MAX_SENDS_PER_HOUR = 5   # codes per address per hour
CODES_FILE = os.path.join(DATA_DIR, 'auth_codes.json')
VOL_TAB = 'Volunteers'
VOL_COLS = ['email', 'name', 'note', 'added', 'added_by']

# What a volunteer may open. Everything else redirects home.
VOLUNTEER_ENDPOINTS = {
    'home', 'logout', 'static', 'members.home', 'members.enter',
    'bud.home', 'bud.detail',
    'fr.home', 'fr.new', 'fr.detail', 'fr.ask', 'fr.receipts', 'fr.delete',
    'fr.in_list', 'fr.in_new', 'fr.in_detail', 'fr.in_files', 'fr.in_delete',
    'inv.home', 'inv.new', 'inv.detail', 'inv.pdf',
}
# Reachable without signing in: the sign-in itself, and the emailed approval and confirmation links.
PUBLIC_ENDPOINTS = {'home', 'static', 'logout', 'fr.approve_link', 'board.vote_link', 'inv.delete_confirm', 'auth.google', 'auth.google_back', 'privacy'}


# ---------- who is who ----------

def volunteers():
    """{email: {email, name, note, added, added_by}} from the Volunteers tab; {} without Sheets."""
    if not hasattr(store, '_records'):
        return {}
    def load():
        try:
            return {r['email'].strip().lower(): r for r in store._records(f'{VOL_TAB}!A:E') if r.get('email', '').strip()}
        except Exception as e:
            logging.error(f"Volunteers list unavailable: {e}")
            return {}
    return store._cached('volunteers', load)


def lookup(email):
    """{'email', 'name', 'level', 'roles'} for an address, or None if it may not sign in."""
    email = (email or '').strip().lower()
    if not email:
        return None
    mine = [o for o in O.all() if (o.get('email') or '').strip().lower() == email]
    if mine:
        # A bracketed entry ("Operations approver (Treasurer)") only routes approvals to that job's holder; it is
        # not a role of its own, so it adds no permissions (the same roles the Users page shows).
        alltitles = {o.get('title', '') for o in O.all()}
        roles = sorted({o.get('role', '') for o in mine if not O.follows(o.get('title', ''), alltitles)})
        import access
        perms = access.perms_for(roles)
        level = 'officer' if 'board' in perms else 'official'
        return {'email': email, 'name': mine[0]['name'], 'level': level, 'roles': roles, 'perms': sorted(perms)}
    v = volunteers().get(email)
    if v:
        import access
        return {'email': email, 'name': v.get('name') or email, 'level': 'volunteer', 'roles': [access.VOLUNTEER],
                'perms': sorted(access.perms(access.VOLUNTEER))}
    return None


def group_address(email):
    """The role group an official's app mail goes to (the first role in the officials list that has one); their own
    address only when none of their roles has a group, and for volunteers. Sign-in codes do NOT use this: a code
    proves who you are, so it always goes to your own address."""
    email = (email or '').strip().lower()
    mine = [o for o in O.all() if (o.get('email') or '').strip().lower() == email]
    if mine:
        return next((O.mailto(o) for o in mine if O.mailto(o)), email)
    return email


def user():
    """The signed-in person for this request, or None."""
    if 'auth_user' not in g:
        g.auth_user = lookup(session.get('email'))
    return g.auth_user


def limited(what='funds'):
    """True when lists show only the person's own funds requests (what='funds') or invoices ('invoices'):
    their roles lack "See every ..." (Admin > Roles)."""
    u = user()
    return bool(u and f'{what}_all' not in (u.get('perms') or ()))


# Pages that need an everyday permission (Admin > Roles), for officials and volunteers alike. Looking is never gated.
ENDPOINT_PERMS = {
    'index': 'events', 'bud.edit': 'events', 'bud.actuals': 'events', 'bud.delete': 'events', 'bud.tie': 'events',
    'edit_event': 'events', 'delete_event': 'events',
    'fr.new': 'funds', 'fr.receipts': 'funds', 'fr.ask': 'funds', 'fr.in_new': 'funds', 'fr.in_files': 'funds',
    'inv.new': 'invoices',
    'members.enter': 'points_enter',
    'board.motions': 'board_ask',
}


def owns_request(r):
    u = user()
    return bool(u and r and ((r.get('submitter_email') or '').strip().lower() == u['email']
                             or (r.get('entered_by') or '').strip().lower() == u['name'].lower()))


def owns_invoice(inv):
    u = user()
    return bool(u and inv and (inv.get('created_by') or '').strip().lower() == u['name'].lower())


# ---------- every request ----------

def gate():
    """before_request: keep the old session keys in step with the signed-in person, and keep volunteers in their lane."""
    if getattr(g, 'api_user', None) or request.endpoint is None:
        return None
    ep = request.endpoint
    if ep.startswith(('auth.', 'api.')) or ep == 'static':   # the API checks its own key
        return None
    if session.get('authenticated') and not session.get('email'):
        # Signed in with the old shared password: that is gone, sign in again by email.
        session.clear()
    u = user()
    if session.get('email') and not u:
        # Removed from the lists since signing in.
        session.clear()
        flash('Your address no longer has access to the apps. Ask the admin (admin@drscca.org) if that is a mistake.', 'warning')
        return redirect(url_for('home'))
    if not u:
        session.pop('authenticated', None)
        return None if ep in PUBLIC_ENDPOINTS else redirect(url_for('home', next=request.full_path.rstrip('?')))
    # The rest of the app reads these: who signs each change, and the officer and Operations-editor checks.
    session['authenticated'] = True
    session['who'] = u['name']
    if u['level'] == 'officer':
        session['officer'] = {'email': u['email'], 'name': u['name']}
    else:
        session.pop('officer', None)
    if 'ops' in u.get('perms', ()):   # set per role in Admin > Roles
        session['equip_editor'] = {'email': u['email'], 'name': u['name']}
    else:
        session.pop('equip_editor', None)
    if u['level'] == 'volunteer' and ep not in VOLUNTEER_ENDPOINTS:
        flash('That part of the apps is for region officials.', 'warning')
        return redirect(url_for('home'))
    need = ENDPOINT_PERMS.get(ep)
    if need and (request.method == 'POST' or ep in ('index', 'fr.new', 'fr.in_new', 'inv.new', 'members.enter', 'bud.edit')) \
            and need not in (u.get('perms') or ()):
        flash("Your role doesn't include that. Ask the admin if you need it.", 'warning')
        return redirect(request.referrer if (request.referrer or '').startswith(request.host_url) else url_for('home'))
    if ep in ('fr.detail', 'fr.ask', 'fr.receipts') and limited('funds'):
        import fr_store
        if not owns_request(fr_store.get(request.view_args.get('req_id'))):
            abort(403)
    if ep in ('inv.detail', 'inv.pdf') and limited('invoices'):
        import inv_store
        if not owns_invoice(inv_store.get(request.view_args.get('inv_id'))):
            abort(403)
    return None


# ---------- codes ----------

def _hash(email, code):
    return hmac.new(current_app.secret_key.encode(), f'{email}|{code}'.encode(), hashlib.sha256).hexdigest()


def _codes(fn):
    """Run fn(codes dict) under the file lock and save what it leaves."""
    with FileLock(CODES_FILE + '.lock'):
        try:
            with open(CODES_FILE) as f:
                codes = json.load(f)
        except (OSError, ValueError):
            codes = {}
        now = time.time()
        codes = {k: v for k, v in codes.items() if v.get('exp', 0) > now or any(t > now - 3600 for t in v.get('sent', []))}
        out = fn(codes, now)
        tmp = CODES_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(codes, f)
        os.replace(tmp, CODES_FILE)
        return out


def _issue(email):
    """A new code for the address, or None when it has had too many this hour."""
    code = f'{secrets.randbelow(10 ** 6):06d}'
    def put(codes, now):
        c = codes.get(email, {})
        sent = [t for t in c.get('sent', []) if t > now - 3600]
        if len(sent) >= MAX_SENDS_PER_HOUR:
            return None
        codes[email] = {'h': _hash(email, code), 'exp': now + CODE_MINUTES * 60, 'tries': 0, 'sent': sent + [now]}
        return code
    return _codes(put)


def _check(email, code):
    """True once for the right, unexpired code; the code is then used up. Wrong guesses count toward MAX_TRIES."""
    code = ''.join(ch for ch in (code or '') if ch.isdigit())
    def take(codes, now):
        c = codes.get(email)
        if not c or c.get('exp', 0) < now or c.get('tries', 0) >= MAX_TRIES:
            return False
        if hmac.compare_digest(c['h'], _hash(email, code)):
            c['exp'] = 0
            return True
        c['tries'] = c.get('tries', 0) + 1
        return False
    return _codes(take)


def _safe_next(target):
    from utils import local_path
    return local_path(target) or ''


@bp.route('/signin', methods=['POST'])
def send_code():
    email = (request.form.get('email') or '').strip().lower()
    nxt = _safe_next(request.form.get('next'))
    if '@' not in email or len(email) > 200:
        flash('Enter your email address.', 'warning')
        return redirect(url_for('home', next=nxt or None))
    u = lookup(email)
    if u:
        code = _issue(email)
        if code:
            link = f"{request.url_root.rstrip('/')}{url_for('auth.verify', e=email, c=code, next=nxt or None)}"
            region = os.environ.get('SCCA_REGION_NAME', 'SCCA')
            send_mail(f'{code} is your {region} apps code',
                      f"Your sign-in code is {code}\n\nOr tap this link on the device you are signing in on:\n{link}\n\n"
                      f"It works once, for {CODE_MINUTES} minutes. If you did not ask for it, ignore this email.", email)
            log_audit_event('SIGNIN_CODE_SENT', u['name'], {'email': email})
        else:
            logging.warning(f"Sign-in codes rate limited for {email}")
    session['signin_email'] = email
    return redirect(url_for('auth.verify', next=nxt or None))


@bp.route('/signin/code', methods=['GET', 'POST'])
def verify():
    nxt = _safe_next(request.values.get('next'))
    if request.method == 'POST':
        email = (request.form.get('email') or session.get('signin_email') or '').strip().lower()
        u = lookup(email)
        if u and _check(email, request.form.get('code')):
            session.clear()
            session.permanent = True
            session['email'] = email
            log_audit_event('SIGNIN', u['name'], {'email': email, 'level': u['level']})
            return redirect(nxt or url_for('home'))
        flash('That code is not right or has expired. Check the latest email, or send a new code.', 'danger')
        return redirect(url_for('auth.verify', next=nxt or None))
    # GET: the code page, or the emailed link (which only fills the form in: mail scanners that open links
    # must not use up the code, so signing in still takes a tap on the button).
    return render_template('home.html', title='Sign in', view='code', next_url=nxt,
                           email=(request.args.get('e') or session.get('signin_email') or '').strip().lower(),
                           code=request.args.get('c', ''), minutes=CODE_MINUTES)


# ---------- or sign in with Google (an option next to the emailed code) ----------
# Turned on by GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (an OAuth web client; its redirect URI is
# <site>/signin/google/back). Google only proves the address: the same lists decide who gets in, as with a code.
GOOGLE_AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN = 'https://oauth2.googleapis.com/token'


def google_on():
    return bool(os.environ.get('GOOGLE_OAUTH_CLIENT_ID') and os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET'))


def _google_back_url():
    return request.url_root.rstrip('/') + url_for('auth.google_back')


@bp.route('/signin/google')
def google():
    if not google_on():
        return redirect(url_for('home'))
    from urllib.parse import urlencode
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    session['g_signin'] = {'state': state, 'nonce': nonce, 'next': _safe_next(request.args.get('next'))}
    q = {'client_id': os.environ['GOOGLE_OAUTH_CLIENT_ID'], 'redirect_uri': _google_back_url(), 'response_type': 'code',
         'scope': 'openid email', 'state': state, 'nonce': nonce, 'prompt': 'select_account'}
    return redirect(f'{GOOGLE_AUTH}?{urlencode(q)}')


@bp.route('/signin/google/back')
def google_back():
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token
    want = session.pop('g_signin', None) or {}
    if not google_on() or not want or not hmac.compare_digest(request.args.get('state', ''), want.get('state', '')) or not request.args.get('code'):
        flash('Google sign-in did not finish. Try again, or use the emailed code.', 'warning')
        return redirect(url_for('home'))
    try:
        r = requests.post(GOOGLE_TOKEN, data={'code': request.args['code'], 'client_id': os.environ['GOOGLE_OAUTH_CLIENT_ID'],
                                              'client_secret': os.environ['GOOGLE_OAUTH_CLIENT_SECRET'],
                                              'redirect_uri': _google_back_url(), 'grant_type': 'authorization_code'}, timeout=20)
        r.raise_for_status()
        info = id_token.verify_oauth2_token(r.json()['id_token'], Request(), os.environ['GOOGLE_OAUTH_CLIENT_ID'])
    except Exception as e:
        logging.warning(f'Google sign-in failed: {e}')
        flash('Google sign-in did not work. Use the emailed code instead.', 'warning')
        return redirect(url_for('home'))
    email = (info.get('email') or '').strip().lower()
    if not info.get('email_verified') or info.get('nonce') != want.get('nonce'):
        flash('Google did not confirm that address. Use the emailed code instead.', 'warning')
        return redirect(url_for('home'))
    u = lookup(email)
    if not u:
        flash(f'You picked the Google account {email}, which is not on the apps list. Choose your own account instead '
              f'(Sign in with Google lets you pick), or use the email code with the address the region has for you.', 'warning')
        return redirect(url_for('home'))
    session.clear()
    session.permanent = True
    session['email'] = email
    log_audit_event('SIGNIN', u['name'], {'email': email, 'level': u['level'], 'with': 'Google'})
    return redirect(want.get('next') or url_for('home'))


# ---------- the Volunteers list, kept by officers ----------

def _vol_ws():
    from storage import worksheet
    import gspread
    try:
        return worksheet(store.sheet_id, VOL_TAB)
    except gspread.exceptions.WorksheetNotFound:
        from storage import open_book
        ws = open_book(store.sheet_id).add_worksheet(VOL_TAB, rows=200, cols=len(VOL_COLS))
        ws.update([VOL_COLS], 'A1', value_input_option='RAW')
        return ws


def set_volunteer(email, name=None, note='', by=''):
    """Add or update (name given) or remove (name None) a volunteer on the Volunteers tab. Returns what changed."""
    email = (email or '').strip().lower()
    ws = _vol_ws()
    col = [c.strip().lower() for c in ws.col_values(1)]
    out = ''
    if name is None:
        if email in col:
            ws.delete_rows(col.index(email) + 1)
            out = 'removed'
    elif email in col:
        ws.update([[email, name]], f'A{col.index(email) + 1}', value_input_option='RAW')
        out = 'updated'
    else:
        ws.append_row([email, name, (note or '')[:200], date.today().isoformat(), by], value_input_option='RAW', table_range='A1')
        out = 'added'
    store._cache.pop('volunteers', None)
    store._invalidate()
    return out


@bp.route('/access', methods=['GET', 'POST'])
def access():
    u = user()
    if not u or 'access' not in u.get('perms', ()):
        flash('Adding volunteers is for the roles the admin allows it.', 'warning')
        return redirect(url_for('home'))
    if not hasattr(store, 'sheet_id'):
        flash('The volunteers list needs the Google Sheets storage.', 'warning')
        return redirect(url_for('home'))
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        if request.form.get('action') == 'remove':
            if set_volunteer(email, None):
                log_audit_event('ACCESS_REMOVED', u['name'], {'email': email})
                flash(f'Removed {email}.', 'success')
        else:
            name = (request.form.get('name') or '').strip()[:80]
            if '@' not in email or not name:
                flash('A volunteer needs a name and an email address.', 'warning')
            elif lookup(email):
                flash(f'{email} can already sign in.', 'info')
            else:
                set_volunteer(email, name, (request.form.get('note') or '').strip(), u['name'])
                log_audit_event('ACCESS_ADDED', u['name'], {'email': email, 'name': name})
                flash(f'{name} can now sign in with {email}.', 'success')
        return redirect(url_for('auth.access'))
    return render_template('home.html', title='Volunteers', app_name='Volunteers', view='access',
                           vols=sorted(volunteers().values(), key=lambda v: v.get('name', '').lower()))
