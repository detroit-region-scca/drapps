from flask import Flask, request, redirect, render_template, url_for, session, flash
import os
import string
import random
import logging
from collections import defaultdict
from dotenv import load_dotenv
from filelock import FileLock
from datetime import datetime

from utils import (
    load_programs, load_events, save_events, log_audit_event, load_audit_logs,
    generate_unique_code, generate_sequence_id, login_required,
    DATA_LOCK_FILE, AUDIT_FILE, DATA_FILE
)

load_dotenv()

# Configure logging to prevent silent errors
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# Mandatory Secret Key in Production
secret = os.environ.get("FLASK_SECRET_KEY")
is_prod = os.environ.get("FLASK_ENV") == "production"
if is_prod and not secret:
    raise ValueError("FLASK_SECRET_KEY is mandatory in production!")

# Prevent session invalidation on dev restarts
app.secret_key = secret or "dev_fallback_secret_key_change_in_prod"

APP_PASSWORD = os.environ.get("APP_PASSWORD")
PORT = int(os.environ.get("PORT", 5858))
HOST = os.environ.get("HOST", "0.0.0.0")
DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "t")

# Cleanup any orphaned temp files
def cleanup_temp_files():
    tmp_file = f"{DATA_FILE}.tmp"
    if os.path.exists(tmp_file):
        try:
            os.remove(tmp_file)
            logging.info(f"Cleaned up orphaned temp file: {tmp_file}")
        except OSError:
            pass

cleanup_temp_files()

@app.route('/login', methods=['POST'])
def login():
    password = request.form.get('password')
    if APP_PASSWORD and password == APP_PASSWORD:
        session['authenticated'] = True
        return redirect(url_for('index'))
    flash('Invalid password', 'danger')
    return redirect(url_for('index'))

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('index'))

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if not session.get('authenticated'):
            return redirect(url_for('index'))

        lock = FileLock(DATA_LOCK_FILE)
        with lock:
            events = load_events()
            program_code = request.form['program'].strip()
            date_str = request.form['date'].strip()
            event_name = request.form['event_name'].strip()
            creator_name = request.form['your_name'].strip()
            custom_tag = request.form.get('custom_tag', '').strip().upper()
            if custom_tag and not custom_tag.startswith('#'):
                custom_tag = '#' + custom_tag

            unique_code = generate_unique_code(program_code, events)
            sequence_id = generate_sequence_id(program_code, date_str, events)

            new_event = {
                'id': ''.join(random.choices(string.ascii_letters + string.digits, k=8)),
                'program_code': program_code, 'event_name': event_name, 'date': date_str,
                'sequence_id': sequence_id, 'unique_code': unique_code, 'creator_name': creator_name,
                'custom_tag': custom_tag
            }

            events.append(new_event)
            save_events(events)
            log_audit_event('EVENT_CREATED', creator_name, new_event)
        flash('Event created successfully!', 'success')
        return redirect(url_for('index', new_event_id=new_event['id']))

    # GET request
    lock = FileLock(DATA_LOCK_FILE)
    with lock:
        events = load_events()
    programs = load_programs()
    recommended_msr_name = None

    grouped_events = defaultdict(list)
    for event in events:
        program_name_key = f"{event['program_code']}: {programs.get(event['program_code'])}"
        grouped_events[program_name_key].append(event)

    sorted_grouped_events = dict(sorted(grouped_events.items()))

    scca_region_acronym = os.environ.get("SCCA_REGION_ACRONYM", "SCCA")
    scca_region_name = os.environ.get("SCCA_REGION_NAME", "SCCA")
    program_directors_text = os.environ.get("PROGRAM_DIRECTORS_TEXT", "For use by SCCA program directors")

    return render_template('index.html', 
                           title=f"{scca_region_name} Event Registration",
                           events=events, programs=programs,
                           recommended_msr_name=recommended_msr_name,
                           grouped_events=sorted_grouped_events,
                           scca_region_acronym=scca_region_acronym,
                           scca_region_name=scca_region_name,
                           program_directors_text=program_directors_text)

@app.route('/audit')
@login_required
def audit_log():
    lock = FileLock(f"{AUDIT_FILE}.lock")
    with lock:
        logs = load_audit_logs()
    scca_region_name = os.environ.get("SCCA_REGION_NAME", "SCCA")
    
    # Handle simple filtering via query parameters
    action_filter = request.args.get('action')
    if action_filter:
        logs = [log for log in logs if log.get('action') == action_filter]
        
    # Handle dynamic limit
    limit_param = request.args.get('limit', '100')
    if limit_param != 'all':
        try:
            limit = int(limit_param)
            logs = logs[:limit]
        except ValueError:
            logs = logs[:100] # fallback to 100 on bad input
    
    for log in logs:
        # Format Timestamp
        try:
            dt = datetime.fromisoformat(log.get('timestamp', ''))
            log['display_time'] = dt.strftime('%m-%d %I:%M %p')
        except ValueError:
            log['display_time'] = log.get('timestamp', '')
            
        # Generate Diff Text with Actor included
        action = log.get('action', '')
        user = log.get('user', 'Unknown')
        details = log.get('details', {})
        diff_text = ''
        
        if action == 'EVENT_EDITED' and isinstance(details, dict):
            orig = details.get('original', {})
            upd = details.get('updated', {})
            diffs = []
            for k in ['program_code', 'date', 'event_name', 'creator_name', 'custom_tag']:
                if orig.get(k) != upd.get(k):
                    diffs.append(f"{k}: '{orig.get(k)}' -> '{upd.get(k)}'")
            diff_text = f"<b>{user}</b> changed: " + (', '.join(diffs) if diffs else 'No changes')
        elif action == 'EVENT_CREATED' and isinstance(details, dict):
            diff_text = f"<b>{user}</b> created: {details.get('event_name', '')} ({details.get('program_code', '')})"
        elif action == 'EVENT_DELETED' and isinstance(details, dict):
            diff_text = f"<b>{user}</b> deleted: {details.get('event_name', '')} ({details.get('program_code', '')})"
        elif action in ('LOGIN_SUCCESS', 'LOGIN_FAILURE') and isinstance(details, dict):
            diff_text = f"IP: {details.get('remote_addr', 'Unknown')}"
        else:
            diff_text = f"<b>{user}</b> performed an action. See details."
            
        log['diff_text'] = diff_text
        
        # Determine Bootstrap Color Class
        if action == 'EVENT_CREATED':
            log['color'] = 'bg-success'
        elif action == 'EVENT_EDITED':
            log['color'] = 'bg-warning text-dark'
        elif action in ('EVENT_DELETED', 'LOGIN_FAILURE'):
            log['color'] = 'bg-danger'
        else:
            log['color'] = 'bg-secondary'
    
    return render_template('index.html', 
                           title=f"{scca_region_name} Audit Log",
                           audit_logs=logs,
                           scca_region_name=scca_region_name)

@app.route('/delete/<event_id>', methods=['POST'])
@login_required
def delete_event(event_id):
    delete_user_name = request.form.get('delete_user_name', 'Unknown').strip()

    lock = FileLock(DATA_LOCK_FILE)
    with lock:
        events = load_events()
        event_to_delete = next((e for e in events if e.get('id') == event_id), None)
        if event_to_delete:
            events = [e for e in events if e.get('id') != event_id]
            save_events(events)
            log_audit_event('EVENT_DELETED', delete_user_name, event_to_delete)
            flash('Event deleted successfully.', 'danger')
        else:
            flash('Event not found.', 'warning')
    return redirect(url_for('index'))

@app.route('/edit/<event_id>', methods=['POST'])
@login_required
def edit_event(event_id):
    lock = FileLock(DATA_LOCK_FILE)
    with lock:
        events = load_events()
        event_to_edit = next((e for e in events if e.get('id') == event_id), None)

        if not event_to_edit:
            flash('Event not found.', 'warning')
            return redirect(url_for('index'))

        original_event = event_to_edit.copy()

        event_to_edit['program_code'] = request.form['program'].strip()
        event_to_edit['date'] = request.form['date'].strip()
        event_to_edit['event_name'] = request.form['event_name'].strip()
        event_to_edit['creator_name'] = request.form['your_name'].strip()
        
        custom_tag = request.form.get('custom_tag', '').strip().upper()
        if custom_tag and not custom_tag.startswith('#'):
            custom_tag = '#' + custom_tag
        event_to_edit['custom_tag'] = custom_tag
        
        if not event_to_edit.get('unique_code') or event_to_edit.get('unique_code') == original_event.get('custom_tag'):
             event_to_edit['unique_code'] = generate_unique_code(event_to_edit['program_code'], events)
        
        save_events(events)
        
        log_audit_event('EVENT_EDITED', request.form['your_name'], {
            'event_id': event_id,
            'original': original_event,
            'updated': event_to_edit
        })
    
    flash('Event updated successfully!', 'success')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=DEBUG)
