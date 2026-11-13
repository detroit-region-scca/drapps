"""Email groups: every drscca.org address and who it reaches, for anyone signed in.

Every night at 2:15 (drscca-groups.timer), and when anyone signed in presses Refresh (drscca-groups.path), the
server reads the groups from Google (tools/groups_pull.py) into data/email-groups.json. Google is the only place the groups live; the apps never change them, with one exception: Subscribe on the Hassles page
adds or removes the signed-in person on hassles@drscca.org (tools/hassles_subscribe.py).
"""
import json
import os
import re
from datetime import datetime

from flask import Blueprint, redirect, render_template, url_for

import auth
import officials
import utils
from utils import login_required

bp = Blueprint('groups', __name__, url_prefix='/email-groups')
GROUPS_FILE = os.path.join(utils.DATA_DIR, 'email-groups.json')
GROUPS_REFRESH = os.path.join(utils.DATA_DIR, 'groups-refresh')
EMPTY_NOTE = {'apps': 'Nobody: DR Apps sends from it', 'hassles': 'Nobody yet: subscribe on the Hassles page', 'legacy': 'The admin account: catches mail to retired addresses'}


def _nice_time(iso):
    try:
        return datetime.fromisoformat(iso).strftime('%b %-d, %-I:%M %p')
    except ValueError:
        return ''


@bp.route('/')
@login_required
def home():
    try:
        with open(GROUPS_FILE) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = {'groups': [], 'updated': ''}
    names = {(o.get('email') or '').lower(): o.get('name') for o in officials.all() if o.get('name')}
    names.update({e: v.get('name') for e, v in auth.volunteers().items() if v.get('name') and e not in names})
    show_email = (auth.user() or {}).get('level') != 'volunteer'
    rows = []
    for gr in doc.get('groups', []):
        # Each person who gets the group's mail: name (when the apps know it) and address. Volunteers see names only.
        to = [{'name': names.get(m['email']) or '', 'email': m['email'] if show_email else ''}
              for m in gr.get('members', []) if m.get('role') != 'OWNER']
        to.sort(key=lambda p: ((p['name'] or p['email'] or '~').lower(), p['email']))
        local = gr['email'].split('@')[0]
        rows.append(dict(gr, label=re.sub(r'^DRSCCA\s+', '', gr.get('name') or ''), to=to,
                         empty=EMPTY_NOTE.get(local, 'Nobody (the admin account only)')))
    refreshing = os.path.exists(GROUPS_REFRESH) and os.path.getmtime(GROUPS_REFRESH) > (
        os.path.getmtime(GROUPS_FILE) if os.path.exists(GROUPS_FILE) else 0)
    return render_template('groups.html', title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Email groups", rows=rows,
                           refreshing=refreshing, updated=_nice_time(doc.get('updated', '')))


@bp.route('/refresh', methods=['POST'])
@login_required
def refresh():
    """Anyone signed in: the server re-reads the groups from Google (drscca-groups.path watches this file). It only
    reads, so it is safe for everyone."""
    with open(GROUPS_REFRESH, 'w') as f:
        f.write(datetime.now().isoformat())
    return redirect(url_for('groups.home'))
