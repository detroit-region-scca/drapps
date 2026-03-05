from flask import Flask, request, redirect, render_template_string, url_for, session
import json
import os
import random
import string
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

APP_PASSWORD = os.environ.get("APP_PASSWORD", "cleanshop")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "events.json")

PROGRAMS = {
    "AX": "Autocross",
    "RR": "Road Racing",
    "RY": "Road Rally",
    "RX": "Rallycross",
    "SS": "Street Survival",
    "SIM": "SIM Racing",
    "DR": "General Events"
}

# --- Data Persistence ---
def load_events():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, 'r') as f:
            # Sort events by date, newest first
            return sorted(json.load(f), key=lambda x: x['date'], reverse=False)
    except (json.JSONDecodeError, FileNotFoundError):
        return []

def save_events(events):
    with open(DATA_FILE, 'w') as f:
        json.dump(events, f, indent=4)

# --- Logic Generators ---
def generate_unique_code(program_code, existing_events):
    while True:
        suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=3))
        # code = f"#{program_code}{suffix}"
        code = f"#DR{suffix}"
        if not any(e['unique_code'] == code for e in existing_events):
            return code

def generate_sequence_id(program_type, date_str, existing_events):
    try:
        year = datetime.strptime(date_str, '%Y-%m-%d').year
    except ValueError:
        year = datetime.now().year
    
    count = sum(1 for e in existing_events if e.get('program_code') == program_type and e.get('date', '').startswith(str(year)))
    
    sequence_num = count + 1
    return f"{year}-{sequence_num:02d}-{program_type}"


# --- HTML Template (Bootstrap 5 Dark Mode) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DRSCCA Event Registration</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #212529; padding-top: 2rem; }
        .container { max-width: 900px; }
        .card { 
            box-shadow: 0 4px 8px rgba(0,0,0,0.4); 
            border: 1px solid #495057; 
            background-color: #343a40;
        }
        .table-responsive { margin-top: 1rem; }
        .delete-btn { color: #dc3545; cursor: pointer; font-weight: bold; border: none; background: none; }
        .delete-btn:hover { color: #f06571; }
        .accordion-button:not(.collapsed) { background-color: #343a40; color: #f8f9fa; }

        /* Consistent column widths for tables */
        .event-table th, .event-table td { font-size: 0.85rem; } /* Smaller font for all table fields */
        .event-table th:nth-child(1), .event-table td:nth-child(1) { width: 17%; } /* Sequence ID */
        .event-table th:nth-child(2), .event-table td:nth-child(2) { width: 22%; } /* Description */
        .event-table th:nth-child(3), .event-table td:nth-child(3) { width: 17%; } /* Creator */
        .event-table th:nth-child(4), .event-table td:nth-child(4) { width: 15%; } /* Date */
        .event-table th:nth-child(5), .event-table td:nth-child(5) { width: 19%; } /* Unique Code */
        .event-table th:nth-child(6), .event-table td:nth-child(6) { width: 10%; } /* Action */
    </style>
</head>
<body>
    {% if not session.get('authenticated') %}
    <div class="modal fade show" tabindex="-1" aria-labelledby="loginModalLabel" aria-modal="true" role="dialog" style="display: block; background: rgba(0,0,0,0.8);">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="loginModalLabel">Login Required</h5>
                </div>
                <form action="/login" method="POST">
                    <div class="modal-body">
                        {% if error %}
                        <div class="alert alert-danger">{{ error }}</div>
                        {% endif %}
                        <div class="mb-3">
                            <label for="password" class="form-label">Password</label>
                            <input type="password" class="form-control" id="password" name="password" required autofocus>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="submit" class="btn btn-primary">Login</button>
                    </div>
                </form>
            </div>
        </div>
    </div>
    {% else %}
    <div class="container">
        <h1 class="mb-0 text-center text-primary">DRSCCA Event Registration</h1>
        <div class="d-flex justify-content-between align-items-center mb-4">
            <p class="text-muted mb-0 mx-auto" style="padding-left: 56px;">For use by DRSCCA program directors</p>
            <form action="/logout" method="POST" class="m-0"><button type="submit" class="btn btn-outline-secondary btn-sm">Logout</button></form>
        </div>

        {% if recommended_msr_name %}
        <div class="alert alert-success" role="alert">
            <h5 class="alert-heading">Recommended MSR Event Name</h5>
            <p id="msr-name" class="mb-0">{{ recommended_msr_name }}</p>
            <hr>
            <button class="btn btn-outline-success btn-sm" onclick="copyText(document.getElementById('msr-name').innerText, 'Copied MSR Name!')">
                Copy to Clipboard
            </button>
        </div>
        {% endif %}
        
        <div class="card p-4 mb-5">
            <form action="/" method="POST">
                <div class="row g-3">
                    <div class="col-md-6"><label for="your_name" class="form-label">Your Name</label><input type="text" class="form-control" id="your_name" name="your_name" placeholder="e.g. John Doe" required></div>
                    <div class="col-md-6"><label for="program" class="form-label">Program Name</label><select class="form-select" id="program" name="program" required><option value="" selected disabled>Select...</option>{% for code, name in programs.items() %}<option value="{{ code }}">{{ code }}: {{ name }}</option>{% endfor %}</select></div>
                    <div class="col-md-6"><label for="date" class="form-label">Event Date</label><input type="date" class="form-control" id="date" name="date" required></div>
                    <div class="col-md-6"><label for="description" class="form-label">Event Description</label><input type="text" class="form-control" id="description" name="description" placeholder="e.g. Summer Heat" required></div>
                </div>
                <div class="mt-4 text-end"><button type="submit" class="btn btn-primary px-4">Submit Event</button></div>
            </form>
        </div>

        <div class="card p-4">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h3 class="card-title mb-0">Registered Events</h3>
                <button class="btn btn-outline-info btn-sm" onclick="toggleAllAccordions(true)">Expand All</button>
            </div>
            <div class="accordion" id="eventAccordion">
                {% for program_name, event_list in grouped_events.items() %}
                <div class="accordion-item">
                    <h2 class="accordion-header" id="heading-{{ loop.index }}">
                        <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapse-{{ loop.index }}" aria-expanded="false" aria-controls="collapse-{{ loop.index }}">
                            {{ program_name }} ({{ event_list|length }} events)
                        </button>
                    </h2>
                    <div id="collapse-{{ loop.index }}" class="accordion-collapse collapse" aria-labelledby="heading-{{ loop.index }}">
                        <div class="accordion-body">
                            <div class="table-responsive">
                                <table class="table table-hover align-middle event-table">
                                    <thead><tr><th>Sequence ID</th><th>Description</th><th>Creator</th><th>Date</th><th>Unique Code</th><th class="text-end">Action</th></tr></thead>
                                    <tbody>
                                        {% for event in event_list %}
                                        <tr>
                                            <td class="fw-bold">{{ event.sequence_id }}</td>
                                            <td>{{ event.description }}</td>
                                            <td>{{ event.creator_name }}</td>
                                            <td>{{ event.date }}</td>
                                            <td>
                                                <span class="badge bg-secondary">{{ event.unique_code }}</span>
                                                <button class="btn btn-outline-secondary btn-sm py-0 px-1 ms-1" onclick="copyText('{{ event.unique_code }}', 'Copied Unique Code!')" title="Copy Code">Copy</button>
                                            </td>
                                            <td class="text-end"><button type="button" class="delete-btn" title="Delete Record" data-bs-toggle="modal" data-bs-target="#deleteModal" data-delete-url="{{ url_for('delete_event', event_id=event.id) }}" data-event-description="{{ event.description }}">✕</button></td>
                                        </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
                {% else %}
                <p class="text-center text-muted py-3">No events registered yet.</p>
                {% endfor %}
            </div>
        </div>
    </div>

    <div class="modal fade" id="deleteModal" tabindex="-1" aria-labelledby="deleteModalLabel" aria-hidden="true">
        <div class="modal-dialog">
            <div class="modal-content"><div class="modal-header"><h5 class="modal-title" id="deleteModalLabel">Confirm Deletion</h5><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button></div>
                <form id="deleteForm" action="" method="POST"><div class="modal-body"><p>To confirm, type the event description: <strong id="descriptionToMatch"></strong></p><input type="text" class="form-control" id="deleteConfirmInput" autocomplete="off" placeholder="Event Description..."></div>
                    <div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button type="submit" class="btn btn-danger" id="confirmDeleteBtn" disabled>Delete</button></div>
                </form>
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        function copyText(text, message) {
            navigator.clipboard.writeText(text).then(() => alert(message)).catch(err => console.error('Failed to copy text: ', err));
        }

        function toggleAllAccordions(expand) {
            const accordionItems = document.querySelectorAll('.accordion-collapse');
            accordionItems.forEach(item => {
                const bsCollapse = new bootstrap.Collapse(item, { toggle: false });
                if (expand) {
                    bsCollapse.show();
                } else {
                    bsCollapse.hide();
                }
            });
        }

        const deleteModal = document.getElementById('deleteModal');
        if (deleteModal) {
            deleteModal.addEventListener('show.bs.modal', function (event) {
                const button = event.relatedTarget;
                const deleteUrl = button.getAttribute('data-delete-url');
                const eventDescription = button.getAttribute('data-event-description');
                
                deleteModal.querySelector('#deleteForm').action = deleteUrl;
                deleteModal.querySelector('#descriptionToMatch').textContent = eventDescription;
                const confirmInput = deleteModal.querySelector('#deleteConfirmInput');
                const confirmBtn = deleteModal.querySelector('#confirmDeleteBtn');
                
                confirmInput.value = '';
                confirmBtn.disabled = true;
                confirmInput.oninput = () => { confirmBtn.disabled = confirmInput.value !== eventDescription; };
            });
        }
    </script>
    {% endif %}
</body>
</html>
"""

# --- Routes ---
@app.route('/login', methods=['POST'])
def login():
    password = request.form.get('password')
    if password == APP_PASSWORD:
        session['authenticated'] = True
        return redirect(url_for('index'))
    return redirect(url_for('index', error='Invalid password'))

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('index'))

@app.route('/', methods=['GET', 'POST'])
def index():
    events = load_events()
    
    if request.method == 'POST':
        if not session.get('authenticated'):
            return redirect(url_for('index'))

        program_code = request.form['program']
        date_str = request.form['date']
        description = request.form['description']
        creator_name = request.form['your_name']
        
        unique_code = generate_unique_code(program_code, events)
        sequence_id = generate_sequence_id(program_code, date_str, events)
        
        new_event = {
            'id': ''.join(random.choices(string.ascii_letters + string.digits, k=8)),
            'program_code': program_code, 'description': description, 'date': date_str,
            'sequence_id': sequence_id, 'unique_code': unique_code, 'creator_name': creator_name
        }
        
        events.append(new_event)
        save_events(events)
        return redirect(url_for('index', new_event_id=new_event['id']))

    recommended_msr_name = None
    new_event_id = request.args.get('new_event_id')
    if new_event_id:
        newly_created_event = next((e for e in events if e.get('id') == new_event_id), None)
        if newly_created_event:
            year = datetime.strptime(newly_created_event['date'], '%Y-%m-%d').year
            program_name = PROGRAMS.get(newly_created_event['program_code'], "Event")
            desc = newly_created_event['description']
            tag = newly_created_event['unique_code']
            recommended_msr_name = f"{year} {program_name}: {desc} {tag}"

    grouped_events = defaultdict(list)
    for event in events:
        program_name_key = f"{event['program_code']}: {PROGRAMS.get(event['program_code'])}"
        grouped_events[program_name_key].append(event)
    
    # Sort grouped events by program name for consistent display
    sorted_grouped_events = dict(sorted(grouped_events.items()))

    return render_template_string(HTML_TEMPLATE, events=events, programs=PROGRAMS, 
                                  recommended_msr_name=recommended_msr_name, 
                                  grouped_events=sorted_grouped_events,
                                  error=request.args.get('error'))

@app.route('/delete/<event_id>', methods=['POST'])
def delete_event(event_id):
    if not session.get('authenticated'):
        return redirect(url_for('index'))

    events = load_events()
    events = [e for e in events if e.get('id') != event_id]
    save_events(events)
    return redirect('/')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5858, debug=True)