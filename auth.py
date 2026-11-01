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

OFFICER_ROLES = ('RE', 'ARE', 'Treasurer')
CODE_MINUTES = 15
MAX_TRIES = 5            # wrong codes before that code is dead
MAX_SENDS_PER_HOUR = 5   # codes per address per hour
CODES_FILE = os.path.join(DATA_DIR, 'auth_codes.json')
VOL_TAB = 'Volunteers'
VOL_COLS = ['email', 'name', 'note', 'added', 'added_by']

# What a volunteer may open. Everything else redirects home.
VOLUNTEER_ENDPOINTS = {
    'home', 'logout', 'static',
    'bud.home', 'bud.detail',
    'fr.home', 'fr.new', 'fr.detail', 'fr.ask', 'fr.receipts',
    'inv.home', 'inv.new', 'inv.detail', 'inv.pdf',
}
# Reachable without signing in: the sign-in itself, and the emailed approval and confirmation links.
PUBLIC_ENDPOINTS = {'home', 'static', 'logout', 'fr.approve_link', 'inv.delete_confirm'}


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
        roles = sorted({o.get('role', '') for o in mine})
        level = 'officer' if set(roles) & set(OFFICER_ROLES) else 'official'
        return {'email': email, 'name': mine[0]['name'], 'level': level, 'roles': roles}
    v = volunteers().get(email)
    if v:
        return {'email': email, 'name': v.get('name') or email, 'level': 'volunteer', 'roles': []}
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


def limited():
    """True for a volunteer: lists show only their own requests and invoices."""
    u = user()
    return bool(u and u['level'] == 'volunteer')


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
        flash('Your address no longer has access to the apps. Ask the treasurer if that is a mistake.', 'warning')
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
    if set(u['roles']) & {'Equipment', 'Treasurer'}:
        session['equip_editor'] = {'email': u['email'], 'name': u['name']}
    else:
        session.pop('equip_editor', None)
    if u['level'] == 'volunteer':
        if ep not in VOLUNTEER_ENDPOINTS:
            flash('That part of the apps is for region officials.', 'warning')
            return redirect(url_for('home'))
        if ep in ('fr.detail', 'fr.ask', 'fr.receipts'):
            import fr_store
            if not owns_request(fr_store.get(request.view_args.get('req_id'))):
                abort(403)
        if ep in ('inv.detail', 'inv.pdf'):
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
    return target if target and target.startswith('/') and not target.startswith('//') else ''


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


@bp.route('/access', methods=['GET', 'POST'])
def access():
    u = user()
    if not u or u['level'] != 'officer':
        flash('The access list is kept by the RE, ARE and treasurer.', 'warning')
        return redirect(url_for('home'))
    if not hasattr(store, 'sheet_id'):
        flash('The volunteers list needs the Google Sheets storage.', 'warning')
        return redirect(url_for('home'))
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        ws = _vol_ws()
        if request.form.get('action') == 'remove':
            col = [c.strip().lower() for c in ws.col_values(1)]
            if email in col:
                ws.delete_rows(col.index(email) + 1)
                log_audit_event('ACCESS_REMOVED', u['name'], {'email': email})
                flash(f'Removed {email}.', 'success')
        else:
            name = (request.form.get('name') or '').strip()[:80]
            if '@' not in email or not name:
                flash('A volunteer needs a name and an email address.', 'warning')
            elif lookup(email):
                flash(f'{email} can already sign in.', 'info')
            else:
                ws.append_row([email, name, (request.form.get('note') or '').strip()[:200], date.today().isoformat(), u['name']],
                              value_input_option='RAW')
                log_audit_event('ACCESS_ADDED', u['name'], {'email': email, 'name': name})
                flash(f'{name} can now sign in with {email}.', 'success')
        store._cache.pop('volunteers', None)
        store._invalidate()
        return redirect(url_for('auth.access'))
    people = [dict(o, level='officer' if o.get('role') in OFFICER_ROLES else 'official') for o in O.all() if o.get('email')]
    return render_template('home.html', title='Who can sign in', app_name='Access', view='access', people=people,
                           vols=sorted(volunteers().values(), key=lambda v: v.get('name', '').lower()))
