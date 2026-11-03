"""Admin: the region's settings, for the admin account (role Admin in the officials list).

- People and roles: who holds each role; change, add or remove a holder. Changes are queued in data/ and applied on the
  server (tools/officials_queue.py, drscca-officials.path): officials.json and the drscca.org Google Groups follow.
- Programs: rename one, or add one (the Registry sheet's Programs tab).
- Sign-in access (volunteers) and the audit log are linked from here.
"""
import os
import re as _re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

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


VOL_PERMS = {'funds', 'invoices', 'points_enter', 'show_members', 'show_funds', 'show_invoices'}    # volunteers only reach these pages
NEEDED = ('RE', 'ARE', 'Secretary', 'Treasurer', 'Admin', 'Director', 'Financial Director', 'Points Keeper')
VOLUNTEER = 'Volunteer'


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
    import access
    kinds = {}
    for o in officials.all():
        kinds.setdefault((o.get('email') or '').strip().lower(), set()).add(o.get('role', ''))
    for u in out.values():
        u['region'] = u['email'].endswith('@drscca.org')
        roles = kinds.get(u['email']) or ({access.VOLUNTEER} if u['volunteer'] else set())
        u['extra'] = sorted(access.perms_for(roles) & set(access.EXTRA))
    return sorted(out.values(), key=lambda u: (u['region'], not u['titles'], u['name'].lower()))


def titles_by_kind():
    """[(kind, [titles])] that can be assigned (not the admin account's own)."""
    kinds = {}
    rows = officials.all()
    titles = {o.get('title', '') for o in rows}
    for o in rows:
        if o.get('role') and o.get('role') != 'Admin' and o.get('title') and not officials.follows(o['title'], titles):
            kinds.setdefault(o['role'], set()).add(o['title'])
    return [(k, sorted(v)) for k, v in sorted(kinds.items())]


@bp.route('/', methods=['GET', 'POST'])
@login_required
def home():
    """Users: everyone who can sign in, and the roles each holds. Roles carry the permissions (Admin > Roles)."""
    if (g := _guard()):
        return g
    if request.method == 'POST':
        return _save_user() if request.form.get('action') == 'user' else _post()
    return render_template('admin.html', **_ctx(view='users', users=users(), kinds=titles_by_kind()))


def _save_user():
    me = _me()
    f = request.form
    old_email = f.get('old_email', '').strip().lower()
    name, email = ' '.join(f.get('name', '').split())[:80], f.get('email', '').strip().lower()
    known = {t for _, ts in titles_by_kind() for t in ts}
    titles = [t for t in f.getlist('title') if t in known]
    alltitles = {o.get('title', '') for o in officials.all()}
    new_title = ' '.join(f.get('new_title', '').split())[:80]
    new_kind = f.get('new_kind', '')
    volunteer = f.get('volunteer') == 'yes' and not titles and not new_title
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
    if new_title and (new_title in known or new_kind not in ('Committee', 'Equipment', 'Director at Large', 'Director')):
        flash('A new role needs a name not already used, and what kind it is.', 'danger')
        return back
    was = everyone.get(old_email) or {'titles': [], 'volunteer': False, 'name': ''}
    was = dict(was, titles=[x for x in was['titles'] if not officials.follows(x, alltitles)])   # those follow their job
    # A role the region needs keeps at least one holder.
    rows = officials.all()
    for t in set(was['titles']) - set(titles):
        kind = next((o.get('role') for o in rows if o.get('title') == t), '')
        holders = {(o.get('email') or '').lower() for o in rows if o.get('title') == t}
        if kind in NEEDED and holders <= {old_email}:
            flash(f'{t} is needed: give it to someone else first (edit that person and tick it).', 'warning')
            return back
    add_new = None
    if new_title:
        add_new = {'role': new_kind, 'title': new_title}
        if new_kind == 'Director' and f.get('program'):
            add_new['program'] = f.get('program')
    official_change = (sorted(was['titles']) != sorted(titles) or add_new
                       or ((was['titles'] or titles) and (name != was['name'] or email != old_email)))
    if official_change:
        utils.queue_official_change({'op': 'person', 'from': old_email, 'name': name, 'email': email, 'titles': titles,
                                     'new': add_new, 'title': ', '.join(titles + ([new_title] if new_title else [])) or '(no roles)',
                                     'by': me.get('name', '')})
    vol = ''
    if volunteer:
        if old_email and old_email != email and was['volunteer']:
            auth.set_volunteer(old_email, None)
        vol = auth.set_volunteer(email, name, f.get('note', ''), me.get('name', ''))
    elif was['volunteer']:
        vol = auth.set_volunteer(old_email or email, None)
    log_audit_event('USER_SAVED', me.get('name', ''), {'email': email, 'was': old_email or '(new)', 'name': name,
                                                       'roles': titles + ([new_title] if new_title else []) + (['Volunteer'] if volunteer else []),
                                                       'had': was['titles'] + (['Volunteer'] if was['volunteer'] else [])})
    if not titles and not new_title and not volunteer:
        flash(f'{name} will no longer be able to sign in{" once the change is saved (within a minute)" if official_change else ""}.', 'success')
    elif official_change:
        flash(f'{name}: roles saved; the officials list and email groups update within a minute.', 'success')
    else:
        flash(f'{name} saved{" as a volunteer" if vol else ""}.', 'success')
    return back


@bp.route('/programs', methods=['GET', 'POST'])
@login_required
def programs():
    if (g := _guard()):
        return g
    if request.method == 'POST':
        return _post()
    return render_template('admin.html', **_ctx(view='programs'))


@bp.route('/roles', methods=['GET', 'POST'])
@login_required
def roles():
    """What each kind of role may do. Set per role, so it follows the job, not the person."""
    if (g := _guard()):
        return g
    import access
    kinds = sorted({o.get('role', '') for o in officials.all() if o.get('role') and o.get('role') != 'Admin'}) + [access.VOLUNTEER]
    if request.method == 'POST':
        role = request.form.get('role', '')
        if role in kinds:
            before = access.perms(role)
            allowed = VOL_PERMS if role == access.VOLUNTEER else set(access.PERMS)
            access.save(role, {p for p in allowed if request.form.get(p) == 'yes'})
            after = access.perms(role)
            if after != before:
                log_audit_event('ROLE_ACCESS_SAVED', _me().get('name', ''), {'role': role, 'added': sorted(after - before), 'removed': sorted(before - after)})
            flash(f'{role} saved.', 'success')
        return redirect(url_for('admin.roles') + f'#{role.replace(" ", "-")}')
    counts = {k: sum(1 for o in officials.all() if o.get('role') == k) for k in kinds}
    counts[access.VOLUNTEER] = len(auth.volunteers())
    titles = {k: sorted({o.get('title', '') for o in officials.all() if o.get('role') == k}) for k in kinds}
    titles[access.VOLUNTEER] = ['added one by one on Users']
    rows = [{'role': k, 'perms': access.perms(k), 'n': counts[k], 'titles': titles[k],
             'choices': VOL_PERMS if k == access.VOLUNTEER else set(access.PERMS)} for k in kinds]
    return render_template('admin.html', **_ctx(view='roles', rows=rows, everyday=access.EVERYDAY, extra=access.EXTRA, apps=access.APPS))


def _post():
    me = _me()
    f = request.form
    act = f.get('action', 'change')
    if act == 'program':
        code, pname = f.get('code', '').strip().upper(), ' '.join(f.get('pname', '').split())
        import re as _re
        if not _re.fullmatch(r'[A-Z]{2,4}', code) or not pname or code == 'OPS':
            flash('A program needs a 2 to 4 letter code (like TT) and a name.', 'danger')
            return redirect(url_for('admin.programs'))
        known = load_programs()
        from storage import store
        store.save_program(code, pname)
        log_audit_event('PROGRAM_SAVED', me.get('name', ''), {'code': code, 'name': pname, 'was': known.get(code, '')})
        flash(f'{code} is now "{pname}".' if code in known else f'Added the program {code} {pname}. Its money needs QuickBooks accounts named "Club {code} {pname} ...".', 'success')
        return redirect(url_for('admin.programs'))
    if act in ('add', 'remove'):
        title = ' '.join(f.get('title', '').split())
        name, email = ' '.join(f.get('name', '').split()), f.get('email', '').strip().lower()
        rows = officials.all()
        same = [o for o in rows if o.get('title') == title]
        if act == 'remove':
            who = next((o for o in same if (o.get('email') or '').lower() == email), None)
            needed = who and who.get('role') in ('RE', 'ARE', 'Secretary', 'Treasurer', 'Admin', 'Director', 'Financial Director', 'Points Keeper')
            if not who:
                flash('Nobody with that address holds that role.', 'warning')
            elif needed and len(same) == 1:
                flash(f'{title} is needed; use Change to give it to someone else instead.', 'warning')
            elif (who.get('email') or '').lower().endswith('@drscca.org'):
                flash('That is a region account; it stays.', 'warning')
            else:
                utils.queue_official_change({'op': 'remove', 'title': title, 'from': who['email'], 'name': who['name'], 'email': who['email'], 'by': me.get('name', '')})
                log_audit_event('OFFICIAL_CHANGE_REQUESTED', me.get('name', ''), {'remove': f"{who['name']} <{who['email']}>", 'title': title})
                flash(f"{who['name']} will be taken off {title} within a minute.", 'success')
            return redirect(url_for('admin.home'))
        if not title or not name or '@' not in email or ' ' in email:
            flash('Give the role, the name and a valid email address.', 'danger')
            return redirect(url_for('admin.home') + '#add')
        if any((o.get('email') or '').lower() == email for o in same):
            flash(f'{name} already holds {title}.', 'info')
            return redirect(url_for('admin.home'))
        base = same[0] if same else {}
        role = base.get('role') or f.get('role', '')
        if role not in ('Equipment', 'Director at Large', 'Director', 'Committee'):
            if not same:
                flash('Pick what kind of role a new one is.', 'danger')
                return redirect(url_for('admin.home') + '#add')
        entry = {k: base[k] for k in ('role', 'program', 'notify') if base.get(k)}
        entry.update(role=role, title=title, name=name, email=email)
        if not same and role == 'Director' and f.get('program'):
            entry['program'] = f.get('program')
        utils.queue_official_change({'op': 'add', 'entry': entry, 'title': title, 'name': name, 'email': email, 'by': me.get('name', '')})
        log_audit_event('OFFICIAL_CHANGE_REQUESTED', me.get('name', ''), {'add': f'{name} <{email}>', 'title': title})
        flash(f'{name} will be added as {title} within a minute.', 'success')
        return redirect(url_for('admin.home'))
    title = f.get('title', '').strip()
    old = next((o for o in officials.all() if o.get('title') == title and (o.get('email') or '').lower() == f.get('from', '').lower()), None)
    name, email = ' '.join(f.get('name', '').split()), f.get('email', '').strip().lower()
    if not old or not name or '@' not in email or ' ' in email:
        flash('Give the name and a valid email address.', 'danger')
        return redirect(url_for('admin.home'))
    if (old.get('email') or '').lower().endswith('@drscca.org'):
        flash('That role uses a region account; change its password holder in Google Workspace instead.', 'warning')
        return redirect(url_for('admin.home'))
    if name == old.get('name') and email == (old.get('email') or '').lower():
        flash('Nothing changed.', 'info')
        return redirect(url_for('admin.home'))
    utils.queue_official_change({'title': title, 'from': old.get('email', ''), 'name': name, 'email': email, 'by': me.get('name', '')})
    log_audit_event('OFFICIAL_CHANGE_REQUESTED', me.get('name', ''), {'title': title, 'from': f"{old.get('name')} <{old.get('email')}>", 'to': f'{name} <{email}>'})
    flash(f'{title}: {name} ({email}) will be saved within a minute, and the role\'s email group updated.', 'success')
    return redirect(url_for('admin.home'))
