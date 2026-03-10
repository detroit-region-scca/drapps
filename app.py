from flask import Flask, request, redirect, render_template_string, url_for, session, flash
import json
import os
import random
import string
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv
from filelock import FileLock
import pytz

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

APP_PASSWORD = os.environ.get("APP_PASSWORD")
PORT = int(os.environ.get("PORT", 5858))
HOST = os.environ.get("HOST", "0.0.0.0")
DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "t")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.environ.get("DATA_FILE", os.path.join(BASE_DIR, "events.ndjson"))
AUDIT_FILE = os.path.join(BASE_DIR, "audit.ndjson")
DATA_LOCK_FILE = os.environ.get("DATA_LOCK_FILE", f"{DATA_FILE}.lock")
PROGRAMS_FILE = os.environ.get("PROGRAMS_FILE", os.path.join(BASE_DIR, "programs.json"))

def load_programs():
    if not os.path.exists(PROGRAMS_FILE):
        return {}
    try:
        with open(PROGRAMS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

# --- Data Persistence ---
def load_events():
    if not os.path.exists(DATA_FILE):
        return []
    events = []
    with open(DATA_FILE, 'r') as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue # Skip malformed lines
    return sorted(events, key=lambda x: x.get('date', ''), reverse=False)

def save_events(events):
    with open(DATA_FILE, 'w') as f:
        for event in events:
            json.dump(event, f)
            f.write('\n')

def log_audit_event(action, user, details):
    est = pytz.timezone('US/Eastern')
    log_entry = {
        "timestamp": datetime.now(est).isoformat(),
        "action": action,
        "user": user,
        "details": details
    }
    with FileLock(f"{AUDIT_FILE}.lock"):
        with open(AUDIT_FILE, 'a') as f:
            json.dump(log_entry, f)
            f.write('\n')

def load_audit_logs():
    if not os.path.exists(AUDIT_FILE):
        return []
    logs = []
    with open(AUDIT_FILE, 'r') as f:
        for line in f:
            try:
                logs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return sorted(logs, key=lambda x: x.get('timestamp', ''), reverse=True)

# --- Logic Generators ---
def generate_unique_code(program_code, existing_events):
    event_code_prefix = os.environ.get("EVENT_CODE_PREFIX", "SCCA")
    while True:
        suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
        code = f"#{event_code_prefix}{suffix}"
        if not any(e['unique_code'] == code for e in existing_events):
            return code

def generate_sequence_id(program_type, date_str, existing_events):
    try:
        year = datetime.strptime(date_str, '%Y-%m-%d').year
    except ValueError:
        year = datetime.now().year

    # Filter events for the same program and year
    relevant_events = [e for e in existing_events if e.get('program_code') == program_type and e.get('date', '').startswith(str(year))]

    if not relevant_events:
        sequence_num = 1
    else:
        # Find the highest existing sequence number
        max_seq = 0
        for e in relevant_events:
            try:
                # Sequence ID format is assumed to be "YEAR-NUM-PROGRAM"
                parts = e.get('sequence_id', '').split('-')
                if len(parts) == 3 and parts[0] == str(year) and parts[2] == program_type:
                    num = int(parts[1])
                    if num > max_seq:
                        max_seq = num
            except (ValueError, IndexError):
                continue # Ignore malformed sequence IDs
        sequence_num = max_seq + 1
        
    return f"{year}-{sequence_num:02d}-{program_type}"


# --- HTML Template ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #121212; color: #e0e0e0; overflow-y: scroll; }
        .card { background-color: #1e1e1e; border-color: #333; }
        .form-control, .form-select { background-color: #2a2a2a; color: #fff; border-color: #444; }
        .form-control:focus, .form-select:focus { background-color: #333; color: #fff; border-color: #0d6efd; box-shadow: none; }
        .table { color: #e0e0e0; }
        .accordion-button { background-color: #2a2a2a; color: #fff; border-color: #444; }
        .accordion-button:not(.collapsed) { background-color: #0d6efd; color: #fff; }
        .accordion-item { border-color: #444; }
        .modal-content { background-color: #1e1e1e; border-color: #444; }
    </style>
</head>
<body class="py-4">
    {% if not session.get('authenticated') %}
    <div class="container d-flex justify-content-center align-items-center" style="min-height: 80vh;">
        <div class="card w-100" style="max-width: 400px;">
            <div class="card-header text-center border-secondary">
                <h4 class="mb-0">Login Required</h4>
            </div>
            <div class="card-body">
                <form action="/login" method="POST">
                    {% if error %}
                    <div class="alert alert-danger">{{ error }}</div>
                    {% endif %}
                    <div class="mb-3">
                        <label for="password" class="form-label">Password</label>
                        <input type="password" class="form-control" id="password" name="password" required autofocus>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">Login</button>
                </form>
            </div>
        </div>
    </div>
    {% else %}
    <div class="container" style="max-width: 900px;">
        <!-- Header & Nav -->
        <div class="d-flex justify-content-between align-items-center mb-4 pb-3 border-bottom border-secondary">
            <h2 class="m-0 text-primary">{{ title }}</h2>
            <div class="d-flex gap-2">
                {% if audit_logs is defined %}
                <a href="{{ url_for('index') }}" class="btn btn-outline-light btn-sm">Main Page</a>
                {% else %}
                <a href="{{ url_for('audit_log') }}" class="btn btn-outline-info btn-sm">Audit Log</a>
                {% endif %}
                <form action="/logout" method="POST" class="m-0">
                    <button type="submit" class="btn btn-outline-danger btn-sm">Logout</button>
                </form>
            </div>
        </div>

        {% if audit_logs is defined %}
        <!-- Audit Log View -->
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h4 class="m-0">Recent Activity</h4>
            <form action="{{ url_for('audit_log') }}" method="GET" class="m-0 d-flex gap-2">
                <select name="action" class="form-select form-select-sm border-secondary bg-dark text-light" onchange="this.form.submit()">
                    <option value="">All Actions</option>
                    <option value="EVENT_CREATED" {% if request.args.get('action') == 'EVENT_CREATED' %}selected{% endif %}>Created</option>
                    <option value="EVENT_EDITED" {% if request.args.get('action') == 'EVENT_EDITED' %}selected{% endif %}>Edited</option>
                    <option value="EVENT_DELETED" {% if request.args.get('action') == 'EVENT_DELETED' %}selected{% endif %}>Deleted</option>
                    <option value="LOGIN_SUCCESS" {% if request.args.get('action') == 'LOGIN_SUCCESS' %}selected{% endif %}>Login Success</option>
                    <option value="LOGIN_FAILURE" {% if request.args.get('action') == 'LOGIN_FAILURE' %}selected{% endif %}>Login Failure</option>
                </select>
                <select name="limit" class="form-select form-select-sm border-secondary bg-dark text-light" onchange="this.form.submit()">
                    <option value="100" {% if request.args.get('limit', '100') == '100' %}selected{% endif %}>Last 100</option>
                    <option value="250" {% if request.args.get('limit') == '250' %}selected{% endif %}>Last 250</option>
                    <option value="500" {% if request.args.get('limit') == '500' %}selected{% endif %}>Last 500</option>
                    <option value="all" {% if request.args.get('limit') == 'all' %}selected{% endif %}>All</option>
                </select>
            </form>
        </div>

        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                    {{ message }}
                    <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <div class="card">
            <div class="card-body p-0 table-responsive">
                <table class="table table-hover mb-0" style="table-layout: fixed;">
                    <thead class="table-dark">
                        <tr>
                            <th style="width: 20%;" class="ps-3">Time (EST)</th>
                            <th style="width: 20%;">Action</th>
                            <th style="width: 60%;" class="pe-3">Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for log in audit_logs %}
                        <tr>
                            <td class="ps-3 text-nowrap"><small>{{ log.display_time }}</small></td>
                            <td><span class="badge {{ log.color }}">{{ log.action }}</span></td>
                            <td class="pe-3">
                                <div class="mb-1">{{ log.diff_text | safe }}</div>
                                <button class="btn btn-sm btn-link p-0 text-decoration-none" type="button" data-bs-toggle="collapse" data-bs-target="#details-{{ loop.index }}">Show Full Details</button>
                                <div class="collapse mt-2" id="details-{{ loop.index }}">
                                    <pre class="mb-0 text-light bg-dark p-2 rounded border border-secondary" style="font-size: 0.8rem; overflow-x: auto;">{{ log.details | tojson(indent=2) }}</pre>
                                </div>
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="3" class="text-center py-4 text-muted">No audit logs found.</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        {% else %}
        <!-- Main Page View -->
        <p class="text-muted text-center mb-3">{{ program_directors_text }}</p>

        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                    {{ message }}
                    <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        {% if recommended_msr_name %}
        <div class="alert alert-success d-flex justify-content-between align-items-center">
            <div>
                <strong>Recommended MSR Event Name:</strong><br>
                <span id="msr-name">{{ recommended_msr_name }}</span>
            </div>
            <button class="btn btn-sm btn-success" onclick="copyText(this, document.getElementById('msr-name').innerText)">Copy</button>
        </div>
        {% endif %}

        <!-- Form Card -->
        <div class="card mb-4">
            <div class="card-body">
                <form action="/" method="POST">
                    <div class="row g-3">
                        <div class="col-md-6">
                            <label class="form-label">Your Name</label>
                            <input type="text" class="form-control" name="your_name" placeholder="e.g. John Doe" required>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Program Name</label>
                            <select class="form-select" name="program" required>
                                <option value="" selected disabled>Select...</option>
                                {% for code, name in programs.items() %}
                                <option value="{{ code }}">{{ code }}: {{ name }}</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Event Date</label>
                            <input type="date" class="form-control" name="date" required>
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Event Description</label>
                            <input type="text" class="form-control" name="event_name" placeholder="e.g. Summer Heat" required>
                        </div>
                        <div class="col-12 text-end mt-4">
                            <button type="submit" class="btn btn-primary px-4">Submit Event</button>
                        </div>
                    </div>
                </form>
            </div>
        </div>

        <!-- Events List -->
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h4 class="m-0">Registered Events</h4>
            <div>
                <button class="btn btn-sm btn-outline-light me-1" onclick="toggleAccordions(true)">Expand All</button>
                <button class="btn btn-sm btn-outline-light" onclick="toggleAccordions(false)">Collapse All</button>
            </div>
        </div>

        <div class="accordion mb-5" id="eventAccordion">
            {% for program_name, event_list in grouped_events.items() %}
            <div class="accordion-item">
                <h2 class="accordion-header">
                    <button class="accordion-button collapsed shadow-none" type="button" data-bs-toggle="collapse" data-bs-target="#collapse-{{ loop.index }}">
                        {{ program_name }} ({{ event_list|length }})
                    </button>
                </h2>
                <div id="collapse-{{ loop.index }}" class="accordion-collapse collapse">
                    <div class="accordion-body p-0">
                        <div class="table-responsive">
                            <table class="table table-hover m-0">
                                <thead class="table-dark">
                                    <tr>
                                        <th>Event Name</th>
                                        <th>Creator</th>
                                        <th>Date</th>
                                        <th class="text-end pe-3">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for event in event_list %}
                                    <tr>
                                        <td>{{ event.event_name }} <span class="badge bg-secondary">{{ event.unique_code }}</span></td>
                                        <td>{{ event.creator_name }}</td>
                                        <td class="text-nowrap">{{ event.date }}</td>
                                        <td class="text-end text-nowrap pe-3 ps-4">
                                            <button class="btn btn-sm btn-outline-info py-0 px-2" onclick="copyText(this, '{{ event.event_name ~ ' ' ~ event.unique_code }}')">Copy</button>
                                            <button class="btn btn-sm btn-outline-warning py-0 px-2 mx-1" 
                                                data-bs-toggle="modal" data-bs-target="#editModal"
                                                data-id="{{ event.id }}" data-creator="{{ event.creator_name }}"
                                                data-program="{{ event.program_code }}" data-date="{{ event.date }}"
                                                data-event-name="{{ event.event_name }}">Edit</button>
                                            <button class="btn btn-sm btn-outline-danger py-0 px-2" 
                                                data-bs-toggle="modal" data-bs-target="#deleteModal" 
                                                data-url="{{ url_for('delete_event', event_id=event.id) }}" 
                                                data-event-name="{{ event.event_name }}">X</button>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
            {% else %}
            <div class="text-center py-4 border rounded border-secondary text-muted">
                No events registered yet.
            </div>
            {% endfor %}
        </div>
        
        <!-- Delete Modal -->
        <div class="modal fade" id="deleteModal" tabindex="-1">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header border-secondary">
                        <h5 class="modal-title">Confirm Deletion</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <form id="deleteForm" action="" method="POST">
                        <div class="modal-body">
                            <div class="mb-3">
                                <label class="form-label">Your Name</label>
                                <input type="text" class="form-control" name="delete_user_name" placeholder="e.g. John Doe" required>
                            </div>
                            <p>Type event name to confirm:<br><strong id="delDesc" class="text-danger"></strong></p>
                            <input type="text" class="form-control" id="delInput" autocomplete="off" placeholder="Event Name...">
                        </div>
                        <div class="modal-footer border-secondary">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="submit" class="btn btn-danger" id="delBtn" disabled>Delete</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <!-- Edit Modal -->
        <div class="modal fade" id="editModal" tabindex="-1">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header border-secondary">
                        <h5 class="modal-title">Edit Event</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <form id="editForm" action="" method="POST">
                        <div class="modal-body">
                            <div class="mb-3">
                                <label class="form-label">Creator</label>
                                <input type="text" class="form-control" id="editCreator" name="your_name" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Program</label>
                                <select class="form-select" id="editProgram" name="program" required>
                                    {% for code, name in programs.items() %}
                                    <option value="{{ code }}">{{ code }}: {{ name }}</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Date</label>
                                <input type="date" class="form-control" id="editDate" name="date" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label">Description</label>
                                <input type="text" class="form-control" id="editEventName" name="event_name" required>
                            </div>
                        </div>
                        <div class="modal-footer border-secondary">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="submit" class="btn btn-primary">Update Event</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        {% endif %} <!-- End of conditional page rendering -->
    </div>
    {% endif %} <!-- End of authenticated check -->

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        function copyText(btn, text) {
            navigator.clipboard.writeText(text);
            let old = btn.innerText;
            btn.innerText = 'Copied!';
            btn.disabled = true;
            setTimeout(() => { btn.innerText = old; btn.disabled = false; }, 1500);
        }

        function toggleAccordions(expand) {
            document.querySelectorAll('.accordion-collapse').forEach(item => {
                let bsCollapse = new bootstrap.Collapse(item, { toggle: false });
                expand ? bsCollapse.show() : bsCollapse.hide();
            });
        }

        const delModal = document.getElementById('deleteModal');
        if(delModal) {
            delModal.addEventListener('show.bs.modal', e => {
                const btn = e.relatedTarget;
                const eventName = btn.getAttribute('data-event-name');
                document.getElementById('deleteForm').action = btn.getAttribute('data-url');
                document.getElementById('delDesc').innerText = eventName;
                const input = document.getElementById('delInput');
                const submitBtn = document.getElementById('delBtn');
                input.value = '';
                submitBtn.disabled = true;
                input.oninput = () => submitBtn.disabled = (input.value !== eventName);
            });
        }

        const editModal = document.getElementById('editModal');
        if(editModal) {
            editModal.addEventListener('show.bs.modal', e => {
                const btn = e.relatedTarget;
                document.getElementById('editForm').action = '/edit/' + btn.getAttribute('data-id');
                document.getElementById('editCreator').value = btn.getAttribute('data-creator');
                document.getElementById('editProgram').value = btn.getAttribute('data-program');
                document.getElementById('editDate').value = btn.getAttribute('data-date');
                document.getElementById('editEventName').value = btn.getAttribute('data-event-name');
            });
        }
    </script>
</body>
</html>
"""

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

        lock = FileLock(DATA_LOCK_FILE)
        with lock:
            events = load_events()
            program_code = request.form['program']
            date_str = request.form['date']
            event_name = request.form['event_name']
            creator_name = request.form['your_name']

            unique_code = generate_unique_code(program_code, events)
            sequence_id = generate_sequence_id(program_code, date_str, events)

            new_event = {
                'id': ''.join(random.choices(string.ascii_letters + string.digits, k=8)),
                'program_code': program_code, 'event_name': event_name, 'date': date_str,
                'sequence_id': sequence_id, 'unique_code': unique_code, 'creator_name': creator_name
            }

            events.append(new_event)
            save_events(events)
            log_audit_event('EVENT_CREATED', creator_name, new_event)
        flash('Event created successfully!', 'success')
        return redirect(url_for('index', new_event_id=new_event['id']))

    # GET request
    events = load_events()
    programs = load_programs()
    recommended_msr_name = None
    new_event_id = request.args.get('new_event_id')
    if new_event_id:
        newly_created_event = next((e for e in events if e.get('id') == new_event_id), None)
        if newly_created_event:
            year = datetime.strptime(newly_created_event['date'], '%Y-%m-%d').year
            program_name = programs.get(newly_created_event['program_code'], "Event")
            desc = newly_created_event['event_name']
            tag = newly_created_event['unique_code']
            recommended_msr_name = f"{year} {program_name}: {desc} {tag}"

    grouped_events = defaultdict(list)
    for event in events:
        program_name_key = f"{event['program_code']}: {programs.get(event['program_code'])}"
        grouped_events[program_name_key].append(event)

    sorted_grouped_events = dict(sorted(grouped_events.items()))

    scca_region_acronym = os.environ.get("SCCA_REGION_ACRONYM", "SCCA")
    scca_region_name = os.environ.get("SCCA_REGION_NAME", "SCCA")
    program_directors_text = os.environ.get("PROGRAM_DIRECTORS_TEXT", "For use by SCCA program directors")

    return render_template_string(HTML_TEMPLATE, 
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
            for k in ['program_code', 'date', 'event_name', 'creator_name']:
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
    
    return render_template_string(HTML_TEMPLATE, 
                                  title=f"{scca_region_name} Audit Log",
                                  audit_logs=logs,
                                  scca_region_name=scca_region_name)

@app.route('/delete/<event_id>', methods=['POST'])
def delete_event(event_id):
    if not session.get('authenticated'):
        return redirect(url_for('index'))

    delete_user_name = request.form.get('delete_user_name', 'Unknown')

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
    return redirect('/')

@app.route('/edit/<event_id>', methods=['POST'])
def edit_event(event_id):
    if not session.get('authenticated'):
        return redirect(url_for('index'))

    lock = FileLock(DATA_LOCK_FILE)
    with lock:
        events = load_events()
        event_to_edit = next((e for e in events if e.get('id') == event_id), None)

        if not event_to_edit:
            flash('Event not found.', 'warning')
            return redirect(url_for('index'))

        original_event = event_to_edit.copy()

        event_to_edit['program_code'] = request.form['program']
        event_to_edit['date'] = request.form['date']
        event_to_edit['event_name'] = request.form['event_name']
        event_to_edit['creator_name'] = request.form['your_name']
        
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
