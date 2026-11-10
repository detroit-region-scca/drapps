"""Minutes: the Secretary (or any officer) uploads board meeting minutes. The app names them the standard way, turns
Word and other documents into PDF, keeps the original, and puts approved minutes in the public Board Minutes folder
that drscca.org lists (shows within the hour). Drafts wait in the private folder until approved."""
import os
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import minutes_logic as L
import minutes_store as S
from utils import log_audit_event, login_required

bp = Blueprint('minutes', __name__, url_prefix='/minutes')


def _me():
    from auth import user
    return user() or {}


def _guard():
    if not S.enabled():
        flash('Minutes isn\'t set up on this server yet. Ask admin@drscca.org.', 'warning')
        return redirect(url_for('home'))
    if 'minutes' not in (_me().get('perms') or ()):
        flash('Minutes are uploaded by the Secretary and the other officers.', 'warning')
        return redirect(url_for('home'))
    return None


def _who():
    return _me().get('name') or session.get('who') or 'Unknown'


@bp.route('/', methods=['GET', 'POST'])
@login_required
def home():
    if (g := _guard()):
        return g
    if request.method == 'POST':
        f = request.form
        up = request.files.get('file')
        try:
            meeting = date.fromisoformat(f.get('meeting', ''))
        except ValueError:
            meeting = None
        kind = f.get('kind') if f.get('kind') in L.KINDS else L.KINDS[0]
        if not meeting or not up or not up.filename:
            flash('Enter the meeting date and choose the minutes file.', 'warning')
            return redirect(url_for('minutes.home'))
        if not L.kind_of_upload(up.filename):
            flash('Upload a PDF, Word (.docx or .doc), OpenDocument, RTF or text file. From Google Docs: File > Download > PDF or Word.', 'warning')
            return redirect(url_for('minutes.home'))
        approved = bool(f.get('approved'))
        data = up.read()
        year = meeting.year
        try:          # convert first: if this fails nothing has been saved yet, so trying again leaves no extra copies
            pdf = S.to_pdf(data, up.filename, os.environ['MINUTES_DRAFTS_FOLDER_ID'])   # temporary files go where the app may delete
        except Exception:
            import logging
            logging.exception(f'Minutes: could not convert {up.filename} to PDF')
            flash('That file could not be turned into a PDF, so nothing was saved. Try again, or save it as a PDF and upload that.', 'danger')
            return redirect(url_for('minutes.home'))
        S.keep_original(year, L.original_name(meeting, kind, up.filename), data, L.mime_for(up.filename))
        if approved:
            out, replaced = S.save(year, L.file_name(meeting, kind), pdf, 'MINUTES_PUBLIC_FOLDER_ID')
            S.save(year, L.file_name(meeting, kind), pdf, 'MINUTES_PRIVATE_FOLDER_ID')
            msg = f"{out['name']} is {'updated' if replaced else 'posted'}; drscca.org shows it within the hour."
        else:
            out, replaced = S.save_draft(L.file_name(meeting, kind, draft=True), pdf)
            msg = f"Saved {out['name']} as a draft. Publish it once the board approves the minutes."
        log_audit_event('MINUTES_UPLOADED', _who(), {'file': out['name'], 'approved': approved, 'from': up.filename})
        flash(msg, 'success')
        return redirect(url_for('minutes.home'))
    t = date.today()
    years = [t.year, t.year - 1]
    pub = []
    for y in years:
        for fl in S.published(y):
            d, label = L.parse(fl['name'])
            pub.append(dict(fl, when=d, label=label, year=y))
    pub.sort(key=lambda x: (x['when'] or date.min), reverse=True)
    return render_template('minutes.html', title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Minutes", page_title='Minutes', kinds=L.KINDS,
                           drafts=S.drafts(), published=pub, public_url=S.public_folder_url(), private_url=S.private_folder_url())


@bp.route('/publish', methods=['POST'])
@login_required
def publish():
    if (g := _guard()):
        return g
    fid = request.form.get('id', '')
    if not fid:
        return redirect(url_for('minutes.home'))
    if fid not in {d['id'] for d in S.drafts()} and not S.is_draft(fid):      # only a file in the drafts folder, whatever the form sends
        flash('That draft is no longer there. Nothing was changed.', 'warning')
        return redirect(url_for('minutes.home'))
    if request.form.get('action') == 'delete':
        name = S.delete_draft(fid)
        log_audit_event('MINUTES_DRAFT_DELETED', _who(), {'file': name})
        flash(f'Deleted the draft {name}.', 'info')
        return redirect(url_for('minutes.home'))
    try:
        out, replaced = S.publish_draft(fid)
    except ValueError:
        flash('That draft cannot be published: its name is not a minutes file name. Upload the minutes again.', 'warning')
        return redirect(url_for('minutes.home'))
    log_audit_event('MINUTES_PUBLISHED', _who(), {'file': out['name']})
    flash(f"{out['name']} is {'updated' if replaced else 'posted'}; drscca.org shows it within the hour.", 'success')
    return redirect(url_for('minutes.home'))
