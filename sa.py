"""Member Milestones: each month's new members and membership anniversaries (the club's service awards).
Writes the public sheet in the Region drive that drscca.org reads; the sheet can also be edited by hand.
The SCCA Member List report can be uploaded to fill a month in: only names and join dates are read from it,
and the file itself is never stored."""
import os

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import sa_logic as L
import sa_store as S
from utils import load_audit_logs, log_audit_event, login_required

bp = Blueprint('sa', __name__, url_prefix='/sa')
SCCA_REPORTS = 'https://my.scca.com/eweb/DynamicPage.aspx?webcode=regionreports'


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Member Milestones", sheet_url=S.sheet_url(),
                milestones=L.MILESTONES, scca_reports=SCCA_REPORTS, **kw)


def _last_roster():
    hit = next((e for e in load_audit_logs() if e.get('action') == 'member_roster_used'), None)
    return hit and {'when': (hit.get('timestamp') or '')[:10], 'who': hit.get('user', '')}


def _page(month, current, from_roster=False):
    return render_template('sa.html', **_ctx(months=L.by_month(S.rows())[:12], month=month, current=current,
                                             from_roster=from_roster, last_roster=_last_roster()))


@bp.route('/', methods=['GET', 'POST'])
@login_required
def home():
    if not S.enabled():
        flash('Member milestones need SERVICE_AWARDS_SHEET_ID and the Google Sheets storage.', 'warning')
        return redirect(url_for('home'))
    if request.method == 'POST':
        month = request.form.get('month', '').strip()
        if not L.valid_month(month):
            flash('Pick the month.', 'warning')
            return redirect(url_for('sa.home'))
        new = L.rows_from_form(month, {y: request.form.get(f'y{y}', '') for y in L.MILESTONES})
        n = S.save_month(month, new)
        log_audit_event('service_awards_saved', session.get('who', 'Unknown'), {'month': month, 'names': n})
        flash(f'Saved {n} names for {month}. drscca.org shows them within the hour.' if n else
              f'Removed {month}.', 'success')
        return redirect(url_for('sa.home', month=month))
    month = request.args.get('month') or L.next_month()
    current = dict(L.by_month(S.rows())).get(month, {}) if L.valid_month(month) else {}
    return _page(month, current)


@bp.route('/roster', methods=['POST'])
@login_required
def roster():
    """Fill a month in from the SCCA Member List report. Nothing is saved until the person checks it and clicks Save."""
    month = request.form.get('month', '').strip()
    f = request.files.get('report')
    if not L.valid_month(month) or not f or not f.filename:
        flash('Pick the month and choose the Member List file.', 'warning')
        return redirect(url_for('sa.home', month=month or None))
    try:
        import openpyxl
        wb = openpyxl.load_workbook(f.stream, read_only=True, data_only=True)
        members = L.roster_members(list(wb.worksheets[0].iter_rows(values_only=True)))
    except ValueError as e:
        flash(str(e), 'warning')
        return redirect(url_for('sa.home', month=month))
    except Exception:
        flash('Could not read that file. Download the Member List again from SCCA (it is an .xlsx file) and retry.', 'warning')
        return redirect(url_for('sa.home', month=month))
    found = L.milestones_for(members, month)
    log_audit_event('member_roster_used', session.get('who', 'Unknown'),
                    {'month': month, 'members_in_report': len(members), 'names_found': sum(len(v) for v in found.values())})
    flash(f'Read {len(members)} members from the report and found {sum(len(v) for v in found.values())} for {month}. '
          'Check the names below, then click Save.', 'info')
    return _page(month, found, from_roster=True)
