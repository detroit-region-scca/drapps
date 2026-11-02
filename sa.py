"""Member Milestones: each month's new members and membership anniversaries (the club's service awards).

Flow: upload the SCCA Member List (kept as-is in a private Drive folder), pick a month, publish. Publishing writes
names only to the public Member Milestones sheet that drscca.org reads; that sheet can also be edited by hand."""
import os

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import sa_logic as L
import sa_store as S
from utils import log_audit_event, login_required

bp = Blueprint('sa', __name__, url_prefix='/sa')
SCCA_REPORTS = 'https://my.scca.com/eweb/DynamicPage.aspx?webcode=regionreports'


def _month():
    m = (request.values.get('month') or '').strip()
    return m if L.valid_month(m) else L.this_month()


@bp.route('/', methods=['GET'])
@login_required
def home():
    if not S.enabled():
        flash('Member milestones need SERVICE_AWARDS_SHEET_ID and the Google Sheets storage.', 'warning')
        return redirect(url_for('home'))
    month = _month()
    published = dict(L.by_month(S.rows())).get(month, {})
    roster, roster_error = None, None
    if S.roster_enabled():
        try:
            roster = S.latest_roster()
        except ValueError as e:
            roster_error = str(e)
    proposed = L.milestones_for(roster['members'], month) if roster else {}
    return render_template('sa.html', title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Member Milestones",
                           month=month, published=published, proposed=proposed, roster=roster, roster_error=roster_error,
                           differs=bool(proposed) and not L.same_names(proposed, published),
                           sheet_url=S.sheet_url(), scca_reports=SCCA_REPORTS, milestones=L.MILESTONES,
                           history=L.by_month(S.rows())[:12])


@bp.route('/upload', methods=['POST'])
@login_required
def upload():
    month = _month()
    f = request.files.get('report')
    if not f or not f.filename:
        flash('Choose the Member List file first.', 'warning')
        return redirect(url_for('sa.home', month=month))
    data = f.read()
    try:
        members = L.read_roster(data)
    except ValueError as e:
        flash(str(e), 'warning')
        return redirect(url_for('sa.home', month=month))
    saved = S.save_roster(data, session.get('who', 'Unknown'))
    log_audit_event('member_list_uploaded', session.get('who', 'Unknown'), {'file': saved['name'], 'members': len(members)})
    flash(f'Member list saved ({len(members)} members). Check the names below and publish.', 'success')
    return redirect(url_for('sa.home', month=month))


@bp.route('/publish', methods=['POST'])
@login_required
def publish():
    """Publish the month from the latest member list (worked out again here, never taken from the browser)."""
    month = _month()
    roster = S.latest_roster()
    if not roster:
        flash('Upload a member list first.', 'warning')
        return redirect(url_for('sa.home', month=month))
    found = L.milestones_for(roster['members'], month)
    rows = [[month, n, y, ''] for y, names in found.items() for n in names]
    S.save_month(month, rows)
    log_audit_event('member_milestones_published', session.get('who', 'Unknown'), {'month': month, 'names': len(rows), 'from': roster['name']})
    flash(f'Published {len(rows)} names for {month}. drscca.org shows them within the hour.', 'success')
    return redirect(url_for('sa.home', month=month))


@bp.route('/edit', methods=['POST'])
@login_required
def edit():
    """Hand edits: replace the month with exactly what is in the boxes."""
    month = _month()
    rows = L.rows_from_form(month, {y: request.form.get(f'y{y}', '') for y in L.MILESTONES})
    S.save_month(month, rows)
    log_audit_event('member_milestones_edited', session.get('who', 'Unknown'), {'month': month, 'names': len(rows)})
    flash(f'Saved {len(rows)} names for {month}.' if rows else f'Removed {month} from drscca.org.', 'success')
    return redirect(url_for('sa.home', month=month))
