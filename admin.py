"""Admin: the region's settings, for the admin account (role Admin in the officials list).

- People and roles: who holds each role; change, add or remove a holder. Changes are queued in data/ and applied on the
  server (tools/officials_queue.py, drscca-officials.path) to officials.json. Email group members are changed only in Google.
- Programs: rename one, or add one (the Registry sheet's Programs tab).
- Volunteers are added and removed only on the App access page (auth.access); the audit log is linked from here.
"""
import json
from datetime import datetime
import os
import re as _re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import access
import auth
import officials
import utils
from utils import load_programs, log_audit_event, login_required

bp = Blueprint('admin', __name__, url_prefix='/admin')


def _me():
    return auth.user() or {}


def _guard():
    if 'admin' not in (_me().get('perms') or []):
        abort(404)            # not even a hint that it exists
    return None


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Admin", people=officials.all(), programs=load_programs(),
                changes=utils.official_changes(), **kw)


VOL_PERMS = {'funds', 'invoices', 'points_enter'}    # volunteers only reach these pages
EXTRA_SHORT = {'board': 'board decisions', 'pay': 'mark paid', 'minutes': 'minutes', 'points': 'worker points', 'ops': 'Operations',
               'access': 'volunteers', 'audit': 'audit log', 'admin': 'admin',
               'no_vote': 'no vote needed', 'hassles': 'hassles'}


def users():
    """Everyone who can sign in, one row per person: officials (by email, with every role title they hold) and
    volunteers. Region accounts (@drscca.org, e.g. the admin account) are listed but not editable here."""
    out = {}
    for o in officials.all():
        e = (o.get('email') or '').strip().lower()
        if not e:
            continue
        u = out.setdefault(e, {'email': e, 'name': o.get('name', ''), 'titles': [], 'volunteer': False})
        u['titles'].append(o.get('title', ''))
    for e, v in auth.volunteers().items():
        u = out.setdefault(e, {'email': e, 'name': v.get('name') or e, 'titles': [], 'volunteer': True, 'note': v.get('note', '')})
        u['volunteer'] = True
    held = {}           # {email: {role: [titles]}}, leaving out entries that follow another job
    for o in officials.own():
        if o.get('role'):
            held.setdefault((o.get('email') or '').strip().lower(), {}).setdefault(o['role'], []).append(o.get('title', ''))
    alltitles = {o.get('title', '') for o in officials.all()}
    for u in out.values():
        u['region'] = u['email'].endswith('@drscca.org')
        u['follows'] = [t for t in u['titles'] if officials.follows(t, alltitles)]
        roles = set(held.get(u['email'], {})) or ({access.VOLUNTEER} if u['volunteer'] else set())
        u['extra'] = [EXTRA_SHORT.get(p, p) for p in access.EXTRA if p in access.perms_for(roles)]
        # The bubbles name the roles exactly as the Roles grid does; the job titles show on hover.
        mine = held.get(u['email'], {})
        u['roles'] = [{'role': 'Admin account' if k == 'Admin' else k, 'titles': mine[k]} for k in sorted(mine, key=access.role_key)]
    return sorted(out.values(), key=lambda u: (u['region'], not u['titles'], u['name'].lower()))


def all_roles():
    return officials.roles()


def titles_by_role():
    """[(role, [titles])] that can be ticked on Users (not the admin account's own). A role nobody holds yet is
    ticked by its own name."""
    out = {}
    for o in officials.own():
        if o.get('role') and o.get('role') != 'Admin' and o.get('title'):
            out.setdefault(o['role'], set()).add(o['title'])
    for k in all_roles():
        out.setdefault(k, {k})
    return [(k, sorted(v)) for k, v in sorted(out.items(), key=lambda kv: access.role_key(kv[0]))]


def _name_taken(name):
    """Why a new role can't use this name, or ''. Names are compared ignoring case, and may not end in
    brackets (those are kept for entries that follow another job, e.g. "Operations approver (Treasurer)")."""
    used = {k.lower() for k in all_roles()} | {'admin', 'admin account', access.VOLUNTEER.lower()} | \
        {(o.get('title') or '').lower() for o in officials.all()}
    if not name:
        return 'Give it a name.'
    if name.lower() in used:
        return f'"{name}" is already a role or job title.'
    if officials.bracketed(name) or ':' in name:
        return 'A name may not end in brackets or contain a colon.'
    return ''


def _pending_roles():
    """Roles that changes still being saved will give to someone."""
    out = set()
    for c in utils.official_changes()['pending']:
        out |= set((c.get('role_of') or c.get('kinds') or {}).values())
    return out


def _removable(role, pending=None):
    """A role can be removed when it was made on Roles and nobody holds it or is about to."""
    if role == access.VOLUNTEER or role in access.ROLE_ORDER or role not in access.saved_roles():
        return False
    if any(o.get('role') == role for o in officials.all()):
        return False
    return role not in (_pending_roles() if pending is None else pending)


@bp.route('/', methods=['GET', 'POST'])
@login_required
def home():
    """Users: everyone who can sign in, and the roles each holds. Roles carry the permissions (Admin > Roles)."""
    if (g := _guard()):
        return g
    if request.method == 'POST':
        act = request.form.get('action')
        if act == 'user':
            return _save_user()
        if act == 'handover':
            return _hand_over()
        flash('That action is no longer used; edit people on Users.', 'warning')
        return redirect(url_for('admin.home'))
    alltitles = {o.get('title', '') for o in officials.all()}
    followers = sorted(x for x in alltitles if officials.follows(x, alltitles))
    return render_template('admin.html', **_ctx(view='users', users=users(), roles=titles_by_role(), followers=followers))


def _hand_over():
    """Give one of a person's roles to someone else in one step (the role is never left empty in between)."""
    me = _me()
    f = request.form
    frm, title = f.get('old_email', '').strip().lower(), f.get('title', '')
    back = redirect(url_for('admin.home'))
    held = next((o for o in officials.all() if (o.get('email') or '').lower() == frm and o.get('title') == title), None)
    if not held or held.get('role') == 'Admin' or frm.endswith('@drscca.org'):
        flash('Pick one of their roles to hand over.', 'warning')
        return back
    to = f.get('to', '').strip().lower()
    if to == 'new':
        email, name = f.get('email', '').strip().lower(), ' '.join(f.get('name', '').split())[:80]
    else:
        email = to
        name = next((u['name'] for u in users() if u['email'] == to), '')
    if not name or '@' not in email or ' ' in email or email == frm or email.endswith('@drscca.org'):
        flash('Pick who gets the role, or give a new name and email.', 'danger')
        return back
    pending = utils.official_changes()['pending']
    busy = {(c.get('from') or '').lower() for c in pending} | {(c.get('email') or '').lower() for c in pending}
    if frm in busy or email in busy:
        flash('A change for one of them is still being saved. Reload in a minute, then try again.', 'warning')
        return back
    utils.queue_official_change({'op': 'change', 'title': title, 'from': frm, 'name': name, 'email': email,
                                 'drop_volunteer': email in auth.volunteers(), 'by': me.get('name', '')})
    log_audit_event('USER_ROLE_HANDED_OVER', me.get('name', ''), {'title': title, 'from': f"{held.get('name')} <{frm}>", 'to': f'{name} <{email}>'})
    flash(f"{title} goes from {held.get('name')} to {name} within a minute. Change the email group at admin.google.com.", 'success')
    return back


def _after_pending(rows, pending):
    """The officials list as it will be once the queued changes are applied."""
    import contextlib
    import io
    import sys
    tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    try:
        from officials_queue import apply_person
    except Exception:          # without the tools, judge by the saved list
        return rows
    rows = [dict(o) for o in rows]
    for c in pending:
        if c.get('op') == 'person':
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rows = apply_person(rows, c)
            except SystemExit:
                pass
    return rows


def _save_user():
    me = _me()
    f = request.form
    old_email = f.get('old_email', '').strip().lower()
    name, email = ' '.join(f.get('name', '').split())[:80], f.get('email', '').strip().lower()
    known = {t for _, ts in titles_by_role() for t in ts}
    titles = [t for t in f.getlist('title') if t in known]
    alltitles = {o.get('title', '') for o in officials.all()}
    role_of = {k: k for k in all_roles() if k in titles and k not in alltitles}     # first holder of a role made on Roles
    back = redirect(url_for('admin.home'))
    if not name or '@' not in email or ' ' in email:
        flash('A user needs a name and a valid email address.', 'danger')
        return back
    everyone = {u['email']: u for u in users()}
    if email.endswith('@drscca.org') or old_email.endswith('@drscca.org'):
        flash('Region accounts (@drscca.org) are managed in Google Workspace, not here.', 'warning')
        return back
    if email != old_email and email in everyone:
        flash(f'{email} is already a user; edit that one instead.', 'warning')
        return back
    was = everyone.get(old_email) or {'titles': [], 'volunteer': False, 'name': ''}
    was = dict(was, titles=[x for x in was['titles'] if not officials.follows(x, alltitles)])   # those follow their job
    volunteer = bool(was['volunteer']) and not titles      # added and removed only on the App access page
    if not titles and not old_email:
        flash('Tick a role. Volunteers are added on the App access page.', 'warning')
        return back
    pending = utils.official_changes()['pending']
    busy = {(c.get('from') or '').lower() for c in pending} | {(c.get('email') or '').lower() for c in pending}
    if (old_email and old_email in busy) or email in busy:
        flash(f'A change for {name} is still being saved. Reload in a minute, then edit again.', 'warning')
        return back
    # A role the region needs keeps at least one holder, counting changes still being saved.
    rows = _after_pending(officials.all(), pending)
    for t in set(was['titles']) - set(titles):
        kind = next((o.get('role') for o in rows if o.get('title') == t), '')
        holders = {(o.get('email') or '').lower() for o in rows if o.get('title') == t}
        if kind in access.NEEDED and holders <= {old_email}:
            flash(f'{t} is needed: give it to someone else first (edit that person and tick it).', 'warning')
            return back
    official_change = (sorted(was['titles']) != sorted(titles)
                       or ((was['titles'] or titles) and (name != was['name'] or email != old_email)))
    if official_change:
        utils.queue_official_change({'op': 'person', 'from': old_email, 'name': name, 'email': email, 'titles': titles,
                                     'role_of': role_of, 'drop_volunteer': bool(was['volunteer'] and titles), 'title': ', '.join(titles) or '(no roles)',
                                     'by': me.get('name', '')})
    vol = ''
    if volunteer and (email != old_email or name != was['name']):
        if old_email != email:
            auth.set_volunteer(old_email, None)
        vol = auth.set_volunteer(email, name, was.get('note', ''), me.get('name', ''))
    # A volunteer given a role stays on the Volunteers tab until the role is saved (the server takes them off then).
    log_audit_event('USER_SAVED', me.get('name', ''), {'email': email, 'was': old_email or '(new)', 'name': name,
                                                       'roles': titles + (['Volunteer'] if volunteer else []),
                                                       'had': was['titles'] + (['Volunteer'] if was['volunteer'] else [])})
    if not titles and not volunteer and was.get('follows'):
        flash(f"{name} has no roles now. They stay listed as {', '.join(was['follows'])} until that job changes hands.", 'success')
    elif not titles and not volunteer:
        flash(f'{name} will no longer be able to sign in{" once the change is saved (within a minute)" if official_change else ""}.', 'success')
    elif official_change:
        flash(f'{name}: roles saved; the officials list updates within a minute. Email groups are changed at admin.google.com.', 'success')
    else:
        flash(f'{name} saved{" as a volunteer" if vol else ""}.', 'success')
    return back


@bp.route('/programs', methods=['GET', 'POST'])
@login_required
def programs():
    if (g := _guard()):
        return g
    if request.method == 'POST':
        return _save_program()
    return render_template('admin.html', **_ctx(view='programs'))


@bp.route('/roles', methods=['GET', 'POST'])
@login_required
def roles():
    """What each kind of role may do. Set per role, so it follows the job, not the person."""
    if (g := _guard()):
        return g
    own = officials.own()
    kinds = all_roles() + [access.VOLUNTEER]
    if request.method == 'POST' and request.form.get('action') == 'add_role':
        name = ' '.join(request.form.get('role', '').split())[:40]
        like = request.form.get('like', '')
        if (why := _name_taken(name)):
            flash(f'New role: {why}', 'danger')
        else:
            access.add_role(name, like if like in kinds else None)
            log_audit_event('ROLE_ADDED', _me().get('name', ''), {'role': name, 'like': like or 'everyday only'})
            officials.mirror_soon()
            flash(f'{name} added. Set what it may do below, then give it to someone on Users.', 'success')
        return redirect(url_for('admin.roles'))
    if request.method == 'POST' and request.form.get('action') == 'remove_role':
        name = request.form.get('role', '')
        if _removable(name):
            access.remove_role(name)
            log_audit_event('ROLE_REMOVED', _me().get('name', ''), {'role': name})
            officials.mirror_soon()
            flash(f'{name} removed.', 'success')
        else:
            flash('Only a role nobody holds (or is about to) can be removed.', 'warning')
        return redirect(url_for('admin.roles'))
    if request.method == 'POST':
        # The whole grid comes back; only roles whose boxes changed are saved, so the rest keep following defaults.
        # Only the columns that were on the page: a role added since (in another tab) has no boxes in this form.
        shown = set(request.form.getlist('roles_shown'))
        changed = []
        for role in (k for k in kinds if k in shown):
            allowed = VOL_PERMS if role == access.VOLUNTEER else set(access.PERMS)
            want = {p for p in allowed if request.form.get(f'{role}::{p}') == 'yes'}
            before = access.perms(role)
            if want == before & allowed:
                continue
            access.save(role, want)
            after = access.perms(role)
            log_audit_event('ROLE_ACCESS_SAVED', _me().get('name', ''), {'role': role, 'added': sorted(after - before), 'removed': sorted(before - after)})
            changed.append(role)
        if changed:
            officials.mirror_soon()
        flash(f"Saved: {', '.join(changed)}." if changed else 'Nothing changed.', 'success' if changed else 'info')
        return redirect(url_for('admin.roles'))
    counts = {k: sum(1 for o in own if o.get('role') == k) for k in kinds}
    counts[access.VOLUNTEER] = len(auth.volunteers())
    titles = {k: sorted({o.get('title', '') for o in own if o.get('role') == k}) for k in kinds}
    titles[access.VOLUNTEER] = ['added one by one on App access']
    pending = _pending_roles()
    cols = [{'role': k, 'perms': access.perms(k), 'n': counts[k], 'titles': titles[k],
             'removable': _removable(k, pending),
             'choices': VOL_PERMS if k == access.VOLUNTEER else set(access.PERMS)} for k in kinds]
    groups = [('Everyday', access.EVERYDAY), ('Extra powers', access.EXTRA)]
    return render_template('admin.html', **_ctx(view='roles', cols=cols, groups=groups))


def _save_program():
    me = _me()
    f = request.form
    if f.get('action') == 'program':
        code, pname = f.get('code', '').strip().upper(), ' '.join(f.get('pname', '').split())
        if not _re.fullmatch(r'[A-Z]{2,4}', code) or not pname or code == 'OPS':
            flash('A program needs a 2 to 4 letter code (like TT) and a name.', 'danger')
            return redirect(url_for('admin.programs'))
        known = load_programs()
        from storage import store
        store.save_program(code, pname)
        log_audit_event('PROGRAM_SAVED', me.get('name', ''), {'code': code, 'name': pname, 'was': known.get(code, '')})
        flash(f'{code} is now "{pname}".' if code in known else f'Added the program {code} {pname}. Its money needs QuickBooks accounts named "Club {code} {pname} ...".', 'success')
        return redirect(url_for('admin.programs'))
    return redirect(url_for('admin.programs'))
