from flask import Flask, request, redirect, render_template, url_for, session, flash
from markupsafe import escape
import json
import os
import random
import string
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv
import pytz

load_dotenv()

app = Flask(__name__)

# Mandatory Secret Key in Production
secret = os.environ.get("FLASK_SECRET_KEY")
is_prod = os.environ.get("FLASK_ENV") == "production"
if is_prod and not secret:
    raise ValueError("FLASK_SECRET_KEY is mandatory in production!")
app.secret_key = secret or os.urandom(24)

APP_PASSWORD = os.environ.get("APP_PASSWORD")
PORT = int(os.environ.get("PORT", 5858))
HOST = os.environ.get("HOST", "0.0.0.0")
DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "t")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROGRAMS_FILE = os.environ.get("PROGRAMS_FILE", os.path.join(BASE_DIR, "programs.json"))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

EVENTS_FILE = os.path.join(DATA_DIR, 'events.ndjson')
AUDIT_FILE = os.path.join(DATA_DIR, 'audit.ndjson')

def load_events():
    events = []
    if os.path.exists(EVENTS_FILE):
        with open(EVENTS_FILE, 'r') as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
    return events

def save_event(event_dict):
    with open(EVENTS_FILE, 'a') as f:
        f.write(json.dumps(event_dict) + '\n')

def update_event(event_id, new_data):
    events = load_events()
    with open(EVENTS_FILE, 'w') as f:
        for ev in events:
            if ev['id'] == event_id:
                ev.update(new_data)
            f.write(json.dumps(ev) + '\n')

def delete_event_by_id(event_id):
    events = load_events()
    with open(EVENTS_FILE, 'w') as f:
        for ev in events:
            if ev['id'] == event_id:
                continue
            f.write(json.dumps(ev) + '\n')

def load_audit_logs():
    logs = []
    if os.path.exists(AUDIT_FILE):
        with open(AUDIT_FILE, 'r') as f:
            for line in f:
                if line.strip():
                    logs.append(json.loads(line))
    return logs

def load_programs():
    if not os.path.exists(PROGRAMS_FILE):
        return {}
    try:
        with open(PROGRAMS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def log_audit_event(action, user, details):
    est = pytz.timezone('US/Eastern')
    log_entry = {
        'timestamp': datetime.now(est).isoformat(),
        'action': action,
        'user': user,
        'details': details
    }
    with open(AUDIT_FILE, 'a') as f:
        f.write(json.dumps(log_entry) + '\n')

# --- Logic Generators ---
def generate_unique_code(program_code):
    event_code_prefix = os.environ.get("EVENT_CODE_PREFIX", "SCCA")
    events = load_events()
    existing_codes = {ev.get('unique_code') for ev in events}
    while True:
        suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
        code = f"#{event_code_prefix}{suffix}"
        if code not in existing_codes:
            return code

def generate_sequence_id(program_type, date_str):
    try:
        year = datetime.strptime(date_str, '%Y-%m-%d').year
    except ValueError:
        year = datetime.now().year

    events = load_events()
    # Filter events for the same program and year
    relevant_events = [e for e in events if e.get('program_code') == program_type and str(e.get('date', '')).startswith(str(year))]

    if not relevant_events:
        sequence_num = 1
    else:
        max_seq = 0
        for e in relevant_events:
            try:
                parts = e.get('sequence_id', '').split('-')
                if len(parts) == 3 and parts[0] == str(year) and parts[2] == program_type:
                    num = int(parts[1])
                    if num > max_seq:
                        max_seq = num
            except (ValueError, IndexError):
                continue
        sequence_num = max_seq + 1
        
    return f"{year}-{sequence_num:02d}-{program_type}"

# --- Routes ---
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

        program_code = request.form['program']
        date_str = request.form['date']
        event_name = request.form['event_name']
        creator_name = request.form['your_name']

        unique_code = generate_unique_code(program_code)
        sequence_id = generate_sequence_id(program_code, date_str)

        new_event = {
            'id': ''.join(random.choices(string.ascii_letters + string.digits, k=8)),
            'program_code': program_code, 
            'event_name': event_name, 
            'date': date_str,
            'sequence_id': sequence_id, 
            'unique_code': unique_code, 
            'creator_name': creator_name
        }
        
        save_event(new_event)
        
        log_audit_event('EVENT_CREATED', creator_name, new_event)
        flash('Event created successfully!', 'success')
        return redirect(url_for('index', new_event_id=new_event['id']))

    # GET request
    events = load_events()
    events.sort(key=lambda e: e.get('date', ''))
    
    programs = load_programs()
    recommended_msr_name = None

    grouped_events = defaultdict(list)
    for event in events:
        program_name_key = f"{event.get('program_code', '')}: {programs.get(event.get('program_code', ''))}"
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
def audit_log():
    if not session.get('authenticated'):
        return redirect(url_for('index'))
    
    logs = load_audit_logs()
    
    # Handle simple filtering via query parameters
    action_filter = request.args.get('action')
    if action_filter:
        logs = [l for l in logs if l.get('action') == action_filter]
        
    # Handle dynamic limit
    logs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    limit_param = request.args.get('limit', '100')
    if limit_param != 'all':
        try:
            limit = int(limit_param)
            logs = logs[:limit]
        except ValueError:
            logs = logs[:100] # fallback to 100 on bad input
    
    scca_region_name = os.environ.get("SCCA_REGION_NAME", "SCCA")
    
    for log in logs:
        # Format Timestamp
        try:
            dt = datetime.fromisoformat(log.get('timestamp', ''))
            log['display_time'] = dt.strftime('%m-%d %I:%M %p')
        except ValueError:
            log['display_time'] = log.get('timestamp', '')
            
        # Generate Diff Text with Actor included
        action = log.get('action', '')
        # Escape user strings to prevent stored XSS
        user = escape(log.get('user', 'Unknown'))
        details = log.get('details', {})
        diff_text = ''
        
        if action == 'EVENT_EDITED' and isinstance(details, dict):
            orig = details.get('original', {})
            upd = details.get('updated', {})
            diffs = []
            for k in ['program_code', 'date', 'event_name', 'creator_name']:
                if orig.get(k) != upd.get(k):
                    diffs.append(f"{escape(k)}: '{escape(orig.get(k))}' -> '{escape(upd.get(k))}'")
            diff_text = f"<b>{user}</b> changed: " + (', '.join(diffs) if diffs else 'No changes')
        elif action == 'EVENT_CREATED' and isinstance(details, dict):
            diff_text = f"<b>{user}</b> created: {escape(details.get('event_name', ''))} ({escape(details.get('program_code', ''))})"
        elif action == 'EVENT_DELETED' and isinstance(details, dict):
            diff_text = f"<b>{user}</b> deleted: {escape(details.get('event_name', ''))} ({escape(details.get('program_code', ''))})"
        elif action in ('LOGIN_SUCCESS', 'LOGIN_FAILURE') and isinstance(details, dict):
            diff_text = f"IP: {escape(details.get('remote_addr', 'Unknown'))}"
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
def delete_event(event_id):
    if not session.get('authenticated'):
        return redirect(url_for('index'))

    delete_user_name = request.form.get('delete_user_name', 'Unknown')

    events = load_events()
    event_to_delete = next((e for e in events if e.get('id') == event_id), None)
    
    if event_to_delete:
        delete_event_by_id(event_id)
        log_audit_event('EVENT_DELETED', delete_user_name, event_to_delete)
        flash('Event deleted successfully.', 'danger')
    else:
        flash('Event not found.', 'warning')
    return redirect('/')

@app.route('/edit/<event_id>', methods=['POST'])
def edit_event(event_id):
    if not session.get('authenticated'):
        return redirect(url_for('index'))

    events = load_events()
    event_to_edit = next((e for e in events if e.get('id') == event_id), None)

    if not event_to_edit:
        flash('Event not found.', 'warning')
        return redirect(url_for('index'))

    original_event = event_to_edit.copy()

    new_data = {
        'program_code': request.form['program'],
        'date': request.form['date'],
        'event_name': request.form['event_name'],
        'creator_name': request.form['your_name']
    }
    
    update_event(event_id, new_data)
    
    event_to_edit.update(new_data)
    
    log_audit_event('EVENT_EDITED', request.form['your_name'], {
        'event_id': event_id,
        'original': original_event,
        'updated': event_to_edit
    })
    
    flash('Event updated successfully!', 'success')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=DEBUG)