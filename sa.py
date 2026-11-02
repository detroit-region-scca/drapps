"""Service Awards: enter each month's membership anniversaries. Writes the public Service Awards sheet that
drscca.org reads; the sheet can also be edited by hand."""
import os

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import sa_logic as L
import sa_store as S
from utils import log_audit_event, login_required

bp = Blueprint('sa', __name__, url_prefix='/sa')


def _ctx(**kw):
    return dict(title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Service Awards", sheet_url=S.sheet_url(),
                milestones=L.MILESTONES, **kw)


@bp.route('/', methods=['GET', 'POST'])
@login_required
def home():
    if not S.enabled():
        flash('Service awards need SERVICE_AWARDS_SHEET_ID and the Google Sheets storage.', 'warning')
        return redirect(url_for('home'))
    if request.method == 'POST':
        month = request.form.get('month', '').strip()
        if not L.valid_month(month):
            flash('Pick the month the awards are for.', 'warning')
            return redirect(url_for('sa.home'))
        new = L.rows_from_form(month, {y: request.form.get(f'y{y}', '') for y in L.MILESTONES})
        n = S.save_month(month, new)
        log_audit_event('service_awards_saved', session.get('who', 'Unknown'), {'month': month, 'awards': n})
        flash(f'Saved {n} awards for {month}. drscca.org shows them within the hour.' if n else
              f'Removed the awards for {month}.', 'success')
        return redirect(url_for('sa.home', month=month))
    months = L.by_month(S.rows())
    month = request.args.get('month') or L.next_month()
    current = dict(months).get(month, {}) if L.valid_month(month) else {}
    return render_template('sa.html', **_ctx(months=months[:12], month=month, current=current))
