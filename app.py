from flask import Flask, request, redirect, render_template, url_for, session, flash, Response
import os
import string
import random
import logging
import hmac
import csv
import io
from collections import defaultdict
from dotenv import load_dotenv
from filelock import FileLock
from datetime import datetime

from utils import (
    load_programs, load_events, save_events, log_audit_event, load_audit_logs,
    generate_unique_code, generate_sequence_id, login_required,
    format_custom_tag, is_tag_unique,
    DATA_LOCK_FILE, AUDIT_FILE, DATA_FILE, send_new_event_email
)

load_dotenv()

# Configure logging to prevent silent errors
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

from wp import bp as wp_bp  # noqa: E402
from fr import bp as fr_bp  # noqa: E402
from bud import bp as bud_bp  # noqa: E402
from plan import bp as plan_bp  # noqa: E402
import wp_store  # noqa: E402
import fr_store  # noqa: E402
import audit_view  # noqa: E402
import bud_store  # noqa: E402
import plan_store  # noqa: E402
app.register_blueprint(wp_bp)
app.register_blueprint(fr_bp)
app.register_blueprint(bud_bp)
app.register_blueprint(plan_bp)
app.config['MAX_CONTENT_LENGTH'] = 40 * 1024 * 1024  # funds request receipts

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

def _safe_next(target):
    """Only same-site paths are followed after login (never an absolute or //host URL)."""
    return target if target and target.startswith('/') and not target.startswith('//') else None

@app.route('/login', methods=['POST'])
def login():
    password = request.form.get('password') or ''
    nxt = _safe_next(request.form.get('next'))
    if APP_PASSWORD and hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        session['authenticated'] = True
        return redirect(nxt or url_for('home'))
    flash('Invalid password', 'danger')
    return redirect(url_for('home', next=nxt) if nxt else url_for('home'))

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('home'))

@app.route('/', methods=['GET', 'POST'])
def home():
    """Login form, or once signed in the apps: Event Register, Worker Points, Funds Requests, Event Budgets."""
    if request.method == 'POST':
        # The create-event form used to post to '/'; a page left open from before keeps working.
        return index()
    return render_template('home.html', title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Apps",
                           wp_enabled=wp_store.enabled(), fr_enabled=fr_store.enabled(), bud_enabled=bud_store.enabled(), plan_enabled=plan_store.enabled(), next_url=_safe_next(request.args.get('next')) or '')

@app.errorhandler(RuntimeError)
def storage_unavailable(e):
    flash(str(e), 'danger')
    return redirect(url_for('index'))

def _budget_codes():
    """Event codes that have a budget, for the Budget buttons on the events list; None hides the buttons."""
    if not bud_store.enabled():
        return None
    try:
        return set(bud_store.budgets())
    except Exception as e:
        logging.error(f"Budgets unavailable for the events list: {e}")
        return None


def _open_plan_lines(events):
    """{"2026|RX": [open plan lines]} for this year and next, for the "Which plan line is this?" list."""
    if not plan_store.enabled():
        return {}
    try:
        y = datetime.now().year
        return {f'{year}|{code}': [{'sort': ln['sort'], 'item': ln['item'], 'month': ln['month'], 'net': ln['net']}
                                   for ln in plan_store.open_lines(str(year), code, events)]
                for year in (y, y + 1) for code in load_programs()}
    except Exception as e:
        logging.error(f"Annual plan unavailable for the events form: {e}")
        return {}


def _link_plan_line(event, sort, who):
    if not sort or not plan_store.enabled():
        return
    code, year = event['custom_tag'] or event['unique_code'], event['date'][:4]
    try:
        if plan_store.link(year, event['program_code'], float(sort), code):
            item = next((ln['item'] for ln in plan_store.plan(year).get(event['program_code'], {}).get('lines', []) if ln['event_code'] == code), '')
            log_audit_event('PLAN_LINKED', who, {'event_code': code, 'year': year, 'program': event['program_code'], 'item': item})
    except Exception as e:
        logging.error(f"Could not tie {code} to the annual plan: {e}")
        flash('The event was created, but tying it to the annual plan failed. Do it from the Annual Plan page.', 'warning')


@app.route('/events', methods=['GET', 'POST'])
def index():
    if not session.get('authenticated'):
        # Before loading anything: an anonymous request should not cost a Sheets read.
        return redirect(url_for('home', next=request.full_path.rstrip('?')))
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
            session['who'] = creator_name
            custom_tag = format_custom_tag(request.form.get('custom_tag'))

            if not is_tag_unique(custom_tag, events):
                session['form_data'] = request.form.to_dict()
                session['tag_error'] = f"Tag '{custom_tag}' is already in use. Please choose a different one."
                return redirect(url_for('index'))

            unique_code = generate_unique_code(program_code, events + [{'custom_tag': custom_tag}])
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
            send_new_event_email(new_event)
        _link_plan_line(new_event, request.form.get('plan_line', ''), creator_name)
        flash('Event created successfully!', 'success')
        return redirect(url_for('index', new_event_id=new_event['id']))

    # GET request
    lock = FileLock(DATA_LOCK_FILE)
    with lock:
        events = load_events()
    programs = load_programs()
    recommended_msr_name = None

    this_year = str(datetime.now().year)
    new_event = next((e for e in events if e['id'] == request.args.get('new_event_id')), None)
    year = request.args.get('year') or ((new_event or {}).get('date') or '')[:4] or this_year
    years = sorted({(e.get('date') or '')[:4] for e in events if (e.get('date') or '')[:4].isdigit()} | {this_year, year}, reverse=True)

    grouped_events = defaultdict(list)
    for event in (e for e in events if (e.get('date') or '').startswith(year)):
        grouped_events[event['program_code']].append(event)

    sorted_grouped_events = {code: sorted(evs, key=lambda e: e.get('date', ''))
                             for code, evs in sorted(grouped_events.items(), key=lambda kv: programs.get(kv[0], kv[0]))}

    scca_region_acronym = os.environ.get("SCCA_REGION_ACRONYM", "SCCA")
    scca_region_name = os.environ.get("SCCA_REGION_NAME", "SCCA")
    program_directors_text = os.environ.get("PROGRAM_DIRECTORS_TEXT", "For use by SCCA program directors")

    form_data = session.pop('form_data', {})
    if not form_data and request.args.get('plan_line'):
        # Arrived from an annual plan line's "Register event": fill the form in from it.
        form_data = {k: request.args[k] for k in ('program', 'date', 'event_name', 'plan_line') if request.args.get(k)}
        if form_data.get('date'):
            flash('Filled in from the annual plan. The day is a placeholder (the 1st); set the real date.', 'info')
    tag_error = session.pop('tag_error', None)

    return render_template('index.html', 
                           title=f"{scca_region_name} Event Registration",
                           events=events, programs=programs,
                           recommended_msr_name=recommended_msr_name,
                           grouped_events=sorted_grouped_events,
                           scca_region_acronym=scca_region_acronym,
                           scca_region_name=scca_region_name,
                           program_directors_text=program_directors_text,
                           form_data=form_data,
                           tag_error=tag_error,
                           year=year, years=years, q=request.args.get('q', ''),
                           budgets=_budget_codes(),
                           open_plan=_open_plan_lines(events))

@app.route('/audit')
@login_required
def audit_log():
    """The shared audit log: every app writes to it, so it is filtered by app and action here."""
    lock = FileLock(f"{AUDIT_FILE}.lock")
    with lock:
        logs = load_audit_logs()
    apps = [label for label, _ in audit_view.APPS.values()]
    actions = sorted({log.get('action', '') for log in logs if log.get('action')})
    app_filter, action_filter = request.args.get('app', ''), request.args.get('action', '')
    if app_filter:
        logs = [log for log in logs if audit_view.app_of(log.get('action', ''))[0] == app_filter]
    if action_filter:
        logs = [log for log in logs if log.get('action') == action_filter]
    limit_param = request.args.get('limit', '100')
    if limit_param != 'all':
        try:
            logs = logs[:int(limit_param)]
        except ValueError:
            logs = logs[:100]
    for log in logs:
        try:
            log['display_time'] = datetime.fromisoformat(log.get('timestamp', '')).strftime('%Y-%m-%d %I:%M %p')
        except ValueError:
            log['display_time'] = log.get('timestamp', '')
        log['app'], log['color'] = audit_view.app_of(log.get('action', ''))
        log['line'] = audit_view.describe(log)
    return render_template('audit.html', title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Audit Log", logs=logs,
                           apps=apps, actions=actions, app_filter=app_filter, action_filter=action_filter, limit=limit_param)

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
    return redirect(url_for('index', year=((event_to_delete or {}).get('date') or '')[:4] or None))

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
        
        custom_tag = format_custom_tag(request.form.get('custom_tag'))
        
        if custom_tag:
            if custom_tag == event_to_edit.get('unique_code'):
                flash(f"Error: Tag '{custom_tag}' is already the generated code for this event.", "danger")
                return redirect(url_for('index', year=original_event['date'][:4] or None))
                
            if not is_tag_unique(custom_tag, events, exclude_event_id=event_id):
                flash(f"Error: Tag '{custom_tag}' is already in use by another event.", "danger")
                return redirect(url_for('index', year=original_event['date'][:4] or None))

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
    return redirect(url_for('index', year=event_to_edit['date'][:4] or None))

@app.route('/export')
@login_required
def export_events():
    lock = FileLock(DATA_LOCK_FILE)
    with lock:
        events = load_events()
    programs = load_programs()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Event ID', 'Program Code', 'Program Name', 'Event Name', 'Date', 'Sequence ID', 'Unique Code', 'Custom Tag', 'Creator'])

    for event in events:
        writer.writerow([
            event.get('id', ''),
            event.get('program_code', ''),
            programs.get(event.get('program_code', ''), ''),
            event.get('event_name', ''),
            event.get('date', ''),
            event.get('sequence_id', ''),
            event.get('unique_code', ''),
            event.get('custom_tag', ''),
            event.get('creator_name', '')
        ])

    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=events.csv'
    return response

if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=DEBUG)
