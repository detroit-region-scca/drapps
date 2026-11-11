from flask import Flask, request, redirect, render_template, url_for, session, flash, Response, g, jsonify
import os
import string
import random
import logging
import calendar
import csv
import gspread
import requests
from urllib.parse import quote
import io
from dotenv import load_dotenv
from filelock import FileLock
from datetime import datetime, timedelta

from utils import (
    load_programs, load_events, save_events, log_audit_event, load_audit_logs,
    generate_unique_code, generate_sequence_id, login_required,
    format_custom_tag, is_tag_unique,
    DATA_LOCK_FILE, AUDIT_FILE, DATA_FILE, send_new_event_email
)

load_dotenv()

# Configure logging to prevent silent errors
logging.basicConfig(level=logging.INFO)

from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
app = Flask(__name__)
# Behind Caddy, the only proxy: links in emails use https and the public host, and request.remote_addr is the
# visitor's own address (the sign-in limits count per address).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Files in static/ are kept by the browser for a year; their links carry the file's change time (?v=...), so a
# changed file is fetched again at once.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 365 * 24 * 3600


@app.url_defaults
def _static_version(endpoint, values):
    if endpoint == 'static' and values.get('filename') and 'v' not in values:
        try:
            values['v'] = int(os.path.getmtime(os.path.join(app.static_folder, values['filename'])))
        except OSError:
            pass


@app.template_filter('wkday')
def wkday(d):
    """'2027-06-14' -> 'Sat'; '' for anything that is not a full date."""
    try:
        return datetime.strptime(d, '%Y-%m-%d').strftime('%a')
    except (TypeError, ValueError):
        return ''


@app.template_filter('nice_date')
def nice_date(d):
    """'2026-11-09' (or a date, or '2026-11-09T14:03...') -> 'Nov 9, 2026'; anything else is shown as it is."""
    if not d:           # '', None or a value the page was never given
        return ''
    try:
        d = d if hasattr(d, 'strftime') else datetime.strptime(str(d)[:10], '%Y-%m-%d')
        return f'{d.strftime("%b")} {d.day}, {d.year}'
    except (TypeError, ValueError):
        return d

from members import bp as members_bp  # noqa: E402
from minutes import bp as minutes_bp  # noqa: E402
from board import bp as board_bp  # noqa: E402
from admin import bp as admin_bp  # noqa: E402
from fr import bp as fr_bp  # noqa: E402
from bud import bp as bud_bp  # noqa: E402
from plan import bp as plan_bp  # noqa: E402
from equip import bp as equip_bp  # noqa: E402
from officer import bp as officer_bp  # noqa: E402
from inv import bp as inv_bp  # noqa: E402
import members_store  # noqa: E402
import fr_store  # noqa: E402
import officials  # noqa: E402
import audit_view  # noqa: E402
import bud_store  # noqa: E402
import plan_store  # noqa: E402
app.register_blueprint(members_bp)
from records import bp as records_bp  # noqa: E402
app.register_blueprint(records_bp)
app.register_blueprint(minutes_bp)
app.register_blueprint(board_bp)
app.register_blueprint(admin_bp)


@app.route('/wp/', defaults={'rest': ''})
@app.route('/wp/<path:rest>')
@app.route('/sa/', defaults={'rest': ''})
@app.route('/sa/<path:rest>')
def old_points_links(rest):
    """Worker Points and Member Milestones became Members; old links (bookmarks, emails) land there."""
    return redirect(url_for('members.home'), code=301)
app.register_blueprint(fr_bp)
app.register_blueprint(bud_bp)
app.register_blueprint(plan_bp)
app.register_blueprint(equip_bp)
app.register_blueprint(inv_bp)
app.register_blueprint(officer_bp)
import api  # noqa: E402
app.before_request(api.signin_from_key)   # a valid API key signs the request in (see api.py)
import auth  # noqa: E402
from markupsafe import Markup  # noqa: E402
app.before_request(auth.gate)               # email-code sign-in: who is signed in and what they may open
import csrf  # noqa: E402
app.before_request(csrf.check)              # forged-form protection (after the API key check, which exempts)
app.after_request(csrf.stamp)
app.register_blueprint(auth.bp)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'   # other sites' pages can't post with the session cookie
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.register_blueprint(api.bp)
import shortlinks  # noqa: E402
app.register_blueprint(shortlinks.bp)
import groups  # noqa: E402
app.register_blueprint(groups.bp)
import hassles  # noqa: E402
app.register_blueprint(hassles.bp)
import treasurer_reports  # noqa: E402
app.register_blueprint(treasurer_reports.bp)
app.config['MAX_CONTENT_LENGTH'] = 40 * 1024 * 1024  # funds request receipts

# Mandatory Secret Key in Production
secret = os.environ.get("FLASK_SECRET_KEY")
is_prod = os.environ.get("FLASK_ENV") == "production"
if is_prod and not secret:
    raise ValueError("FLASK_SECRET_KEY is mandatory in production!")

# Prevent session invalidation on dev restarts
app.secret_key = secret or "dev_fallback_secret_key_change_in_prod"

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
    """The shared password is gone; an old login form posting here goes to the email sign-in."""
    return redirect(url_for('home'))

from utils import qbo_state as _qbo_state  # noqa: E402
app.jinja_env.globals['qbo_state'] = _qbo_state
app.jinja_env.globals['google_on'] = auth.google_on
# The Google Drive logo, put in front of every link that opens Drive or a Google Sheet.
app.jinja_env.globals['drive_icon'] = Markup(
    '<svg class="drive-icon" viewBox="0 0 87.3 78" width="14" height="14" aria-hidden="true" focusable="false">'
    '<path d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z" fill="#0066da"/>'
    '<path d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z" fill="#00ac47"/>'
    '<path d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z" fill="#ea4335"/>'
    '<path d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z" fill="#00832d"/>'
    '<path d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z" fill="#2684fc"/>'
    '<path d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z" fill="#ffba00"/>'
    '</svg>&#8288;')        # a word joiner: the icon never wraps away from its text


def _shows(app_name):
    """Whether an app's tile is on the signed-in person's home page: set per role in Admin > Roles (Apps on the home
    page). Tiles off the role are left out."""
    u = auth.user() or {}
    p = set(u.get('perms') or ())
    return f'show_{app_name}' in p


app.jinja_env.globals['shows'] = _shows


@app.context_processor
def _officials():
    return {'officials': officials, 'me': auth.user()}


@app.route('/privacy')
def privacy():
    """The apps' privacy notice (public; Google sign-in asks for one)."""
    return render_template('privacy.html', title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Apps privacy")


@app.route('/officials')
@login_required
def officials_page():
    """Who holds which region role, read-only. The admin account changes it in the Admin app."""
    return render_template('home.html', title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Officials", app_name='Officials', view='officials',
                           people=officials.shown((auth.user() or {}).get('roles')), programs=load_programs())


@app.route('/treasurer')
@login_required
def treasurer():
    """The treasurer's jobs in one place, each with what is waiting: kept off the directors' pages."""
    import fr
    import fr_logic
    year = str(datetime.now().year)
    jobs = {}
    if plan_store.enabled():
        jobs['books'] = len(plan_store.exceptions())
    if fr_store.enabled():
        a = fr.Approvals()
        waiting = [r for r in a.reqs if r['status'] == 'Submitted']
        ready = [r for r in waiting if fr_logic.ready(a.items(r))]
        jobs['pay'], jobs['approvals'] = (len(ready), sum(r['net'] for r in ready)), len(waiting) - len(ready)
        import received_store
        held = [r for r in received_store.reports() if r['status'] == 'Received']
        jobs['deposit'] = (len(held), sum(r['total'] for r in held), sum(1 for r in held if received_store.single_count(r)))
    if bud_store.enabled():
        # Everything waiting for the board, any year: event and program budgets, and Operations (its plan).
        import bud
        buds = bud_store.budgets()
        years = {str(b.get('date', ''))[:4] for b in buds.values()} | {year, str(int(year) + 1)}
        jobs['budgets'] = sum(1 for b in buds.values() if bud_store.status(b) in ('budget', 'changed'))
        jobs['budgets'] += sum(1 for y in years if y.isdigit() and bud.ops_budget_row(y))
    import equip
    import inv
    jobs['equipment'] = equip.overdue_count()
    jobs['invoices'] = inv.open_counts()
    return render_template('home.html', title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Treasurer", app_name='Treasurer', view='treasurer', jobs=jobs,
                           year=year, members_enabled=members_store.enabled())


@app.route('/treasurer/report')
@login_required
def treasurer_report():
    """The old printable report: replaced by the monthly report to the board; owed to the region and outstanding checks
    are on Books check."""
    return redirect(url_for('treports.home'))


@app.route('/name', methods=['GET', 'POST'])
def name():
    """Names come from the officials and volunteers lists now; nothing to type."""
    return redirect(url_for('home'))


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/', methods=['GET', 'POST'])
def home():
    """Login form, or once signed in the apps home page (the tiles)."""
    if request.method == 'POST':
        # The create-event form used to post to '/'; a page left open from before keeps working.
        return index()
    return render_template('home.html', title=f"{os.environ.get('SCCA_REGION_NAME', 'SCCA')} Apps",
                           members_enabled=members_store.enabled(), minutes_enabled=__import__('minutes_store').enabled(), fr_enabled=fr_store.enabled(), bud_enabled=bud_store.enabled(), plan_enabled=plan_store.enabled(), next_url=auth._safe_next(request.args.get('next')))

def _signed_in_name():
    """Who is signed in, for the audit log: from the officials list, never from a form field."""
    u = auth.user() if 'auth' in globals() else None
    return (u or {}).get('name') or session.get('who') or 'Unknown'


from storage import StorageUnavailable  # noqa: E402


@app.errorhandler(StorageUnavailable)
def storage_unavailable(e):
    """Only the deliberate "storage is down" error becomes a message; any other RuntimeError is a bug and is logged.
    The person goes back to the page they were on, not to a blank Add event form."""
    flash(str(e), 'danger')
    back = request.referrer or ''
    if back.startswith(request.host_url):
        back = '/' + back[len(request.host_url):]
    return redirect(auth._safe_next(back) or url_for('home'))


# Plain pages for the errors a visitor can run into, signed in or not: what happened and a way back.
ERROR_PAGES = {
    403: ("You can't open that page", "Your role doesn't include this page, or it belongs to someone else. "
                                      "Ask the admin (admin@drscca.org) if you need it."),
    404: ('Page not found', 'There is nothing at this address. The link may be old or mistyped.'),
    413: ('That upload is too big', 'Files can add up to 40 MB at most. Go back and attach smaller files, or fewer at a time.'),
    500: ('Something went wrong', 'That did not work, and it was not your doing. Go back and try again; '
                                  'if it keeps happening, tell the admin (admin@drscca.org).'),
}


def _error_page(e):
    code = getattr(e, 'code', 500) if getattr(e, 'code', 500) in ERROR_PAGES else 500
    heading, msg = ERROR_PAGES[code]
    if getattr(g, 'api_user', None) or request.path.startswith('/api/'):
        return jsonify(error=msg), code      # scripts get JSON; any flashed reason stays for api._forward to read
    try:
        return render_template('error.html', title=heading, app_name='Apps', heading=heading, msg=msg), code
    except Exception:
        logging.exception('The error page itself failed')
        return f'{heading}. {msg}', code


for _code in ERROR_PAGES:
    app.register_error_handler(_code, _error_page)


@app.errorhandler(requests.exceptions.RequestException)
def google_drive_error(e):
    """A Drive upload or download failed (network or Google): say so instead of a bare 500."""
    logging.exception(f"Google Drive request failed on {request.path}")
    msg = 'Google Drive did not answer, so that was not saved. Go back, check the form is still filled in, and try again.'
    return render_template('base.html', title='Try again', busy_msg=msg), 503


@app.errorhandler(gspread.exceptions.APIError)
def sheets_busy(e):
    """Google Sheets allows 60 reads a minute; past that (after gspread's own retries) say so instead of a 500."""
    logging.error(f"Google Sheets error on {request.path}: {e}")
    busy = getattr(getattr(e, 'response', None), 'status_code', None) == 429
    msg = ('Google Sheets is busy (too many reads this minute). Wait a minute and reload.' if busy
           else 'Google Sheets returned an error. Reload in a moment; if it keeps happening, tell the admin (admin@drscca.org).')
    return render_template('base.html', title='Busy', busy_msg=msg), 503

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


def maybe_same(events, program, date_str, days=45):
    """Events of the same program within `days` of date_str: maybe the one being registered again."""
    try:
        d = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
    except ValueError:
        return []
    out = []
    for e in events:
        try:
            ed = datetime.strptime(str(e.get('date', ''))[:10], '%Y-%m-%d').date()
        except ValueError:
            continue
        if e.get('program_code') == program and abs((ed - d).days) <= days:
            out.append({'id': e.get('id'), 'code': e.get('custom_tag') or e.get('unique_code'), 'name': e.get('event_name', ''),
                        'date': str(e.get('date', ''))[:10], 'custom_tag': e.get('custom_tag', '')})
    return out


@app.route('/events/new', methods=['GET', 'POST'])
def index():
    """Add event: register an event and get its #DR code. The list of events is the Events page (bud.home)."""
    if not session.get('authenticated'):
        # Before loading anything: an anonymous request should not cost a Sheets read.
        return redirect(url_for('home', next=request.full_path.rstrip('?')))
    if request.method == 'POST':
        lock = FileLock(DATA_LOCK_FILE)
        with lock:
            events = load_events()
            program_code = request.form['program'].strip()
            date_str = request.form['date'].strip()
            event_name = request.form['event_name'].strip()
            creator_name = request.form['your_name'].strip()   # who the event is registered by (may be for someone else)
            custom_tag = format_custom_tag(request.form.get('custom_tag'))

            if not is_tag_unique(custom_tag, events):
                session['form_data'] = request.form.to_dict()
                session['tag_error'] = f"Tag '{custom_tag}' is already in use. Please choose a different one."
                return redirect(url_for('index'))

            # The program may already have this event registered (a stand-in from the annual plan, or a second
            # director): ask before making a second #DR code. "No, it's a new event" posts again with new_ok.
            if not request.form.get('new_ok') and (same := maybe_same(events, program_code, date_str)):
                session['form_data'] = request.form.to_dict()
                session['maybe_same'] = same
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
            log_audit_event('EVENT_CREATED', _signed_in_name(), new_event)
            send_new_event_email(new_event)
        _link_plan_line(new_event, request.form.get('plan_line', ''), creator_name)
        flash(f"Registered {new_event['custom_tag'] or new_event['unique_code']}. Next, set it up on MotorsportReg with "
              f"{new_event['custom_tag'] or new_event['unique_code']} in its name, so the two link up.", 'success')
        return redirect(url_for('bud.detail', code=new_event['custom_tag'] or new_event['unique_code']))

    # GET request
    lock = FileLock(DATA_LOCK_FILE)
    with lock:
        events = load_events()
    programs = load_programs()
    recommended_msr_name = None

    scca_region_acronym = os.environ.get("SCCA_REGION_ACRONYM", "SCCA")
    scca_region_name = os.environ.get("SCCA_REGION_NAME", "SCCA")
    program_directors_text = os.environ.get("PROGRAM_DIRECTORS_TEXT", "For use by SCCA program directors")

    form_data = session.pop('form_data', {})
    if not form_data and (request.args.get('plan_line') or request.args.get('program')):
        # Arrived from an annual plan line's "Register event": fill the form in from it.
        # The plan only knows the month: the date picker opens on it and the director picks the day (no stand-in 1st).
        form_data = {k: request.args[k] for k in ('program', 'event_name', 'plan_line', 'month') if request.args.get(k)}
        try:
            y, m = map(int, form_data.get('month', '').split('-'))
            form_data['month_end'] = f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"
        except ValueError:
            form_data.pop('month', None)
    tag_error = session.pop('tag_error', None)
    same = session.pop('maybe_same', None)

    return render_template('index.html', 
                           title=f"{scca_region_name} Event Registration",
                           events=events, programs=programs,
                           recommended_msr_name=recommended_msr_name,
                           scca_region_acronym=scca_region_acronym,
                           scca_region_name=scca_region_name,
                           program_directors_text=program_directors_text,
                           form_data=form_data,
                           tag_error=tag_error, maybe_same=same,
                           open_plan=_open_plan_lines(events))

@app.route('/audit')
@login_required
def audit_log():
    """The shared audit log: every app writes to it, so it is filtered by app and action here. Officers only."""
    if 'audit' not in ((auth.user() or {}).get('perms') or ()):
        flash('The audit log is for the roles the admin allows it.', 'warning')
        return redirect(url_for('home'))
    lock = FileLock(f"{AUDIT_FILE}.lock")
    with lock:
        logs = load_audit_logs()
    apps = list(dict.fromkeys(label for label, _ in audit_view.APPS.values()))
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

@app.route('/equipment/', defaults={'rest': ''})
@app.route('/equipment/<path:rest>')
def old_equipment(rest):
    """Equipment became Operations; old links keep working."""
    qs = request.query_string.decode()
    return redirect(f"/operations/{quote(rest, safe='/')}" + (f"?{qs}" if qs else ''), 301)


@app.route('/budgets/', defaults={'rest': ''})
@app.route('/budgets/<path:rest>')
def old_budgets(rest):
    """Event Budgets became Events; old links keep working."""
    qs = request.query_string.decode()
    return redirect(f"/events/{quote(rest, safe='/')}" + (f"?{qs}" if qs else ''), 301)


@app.route('/delete/<event_id>', methods=['POST'])
@login_required
def delete_event(event_id):
    delete_user_name = _signed_in_name()

    lock = FileLock(DATA_LOCK_FILE)
    with lock:
        events = load_events()
        event_to_delete = next((e for e in events if e.get('id') == event_id), None)
        if event_to_delete:
            events = [e for e in events if e.get('id') != event_id]
            save_events(events)
            log_audit_event('EVENT_DELETED', delete_user_name, event_to_delete)
            flash('Event deleted.', 'success')
        else:
            flash('Event not found.', 'warning')
    return redirect(url_for('bud.home', year=((event_to_delete or {}).get('date') or '')[:4] or None))

@app.route('/edit/<event_id>', methods=['POST'])
@login_required
def edit_event(event_id):
    lock = FileLock(DATA_LOCK_FILE)
    with lock:
        events = load_events()
        event_to_edit = next((e for e in events if e.get('id') == event_id), None)

        if not event_to_edit:
            flash('Event not found.', 'warning')
            return redirect(url_for('bud.home'))

        original_event = event_to_edit.copy()

        event_to_edit['program_code'] = request.form['program'].strip()
        event_to_edit['date'] = request.form['date'].strip()
        event_to_edit['event_name'] = request.form['event_name'].strip()
        event_to_edit['creator_name'] = request.form['your_name'].strip()
        if 'msr_url' in request.form:
            import msr
            event_to_edit['msr_url'] = msr.clean_url(request.form['msr_url'])
            if request.form['msr_url'].strip() and not event_to_edit['msr_url']:
                flash('That is not a MotorsportReg event link (motorsportreg.com/events/...), so it was not saved.', 'warning')
        
        custom_tag = format_custom_tag(request.form.get('custom_tag'))
        
        if custom_tag:
            if custom_tag == event_to_edit.get('unique_code'):
                flash(f"Error: Tag '{custom_tag}' is already the generated code for this event.", "danger")
                return redirect(url_for('bud.detail', code=original_event.get('custom_tag') or original_event['unique_code']))
                
            if not is_tag_unique(custom_tag, events, exclude_event_id=event_id):
                flash(f"Error: Tag '{custom_tag}' is already in use by another event.", "danger")
                return redirect(url_for('bud.detail', code=original_event.get('custom_tag') or original_event['unique_code']))

        event_to_edit['custom_tag'] = custom_tag
        
        if not event_to_edit.get('unique_code') or event_to_edit.get('unique_code') == original_event.get('custom_tag'):
             event_to_edit['unique_code'] = generate_unique_code(event_to_edit['program_code'], events)
        _remember_code(event_to_edit, original_event.get('custom_tag') or original_event.get('unique_code'))
        
        save_events(events)
        
        log_audit_event('EVENT_EDITED', _signed_in_name(), {
            'event_id': event_id,
            'original': original_event,
            'updated': event_to_edit
        })
        _carry_code(original_event.get('custom_tag') or original_event.get('unique_code'),
                    event_to_edit.get('custom_tag') or event_to_edit['unique_code'])
        # Its budget, funds requests, money received and invoices take the event's program (also mending any left
        # behind when a program was split out).
        import recode
        moved = recode.follow_program(event_to_edit.get('custom_tag') or event_to_edit['unique_code'], event_to_edit['program_code'])
        if moved:
            log_audit_event('EVENT_PROGRAM_FOLLOWED', _signed_in_name(), {'event_id': event_id, 'program': event_to_edit['program_code'],
                                                                          'records': moved})
    
    flash('Event updated.', 'success')
    return redirect(url_for('bud.detail', code=event_to_edit.get('custom_tag') or event_to_edit['unique_code']))


def _remember_code(ev, old):
    """Keep the code an event had before, so a QuickBooks project still named with it goes on matching."""
    now = ev.get('custom_tag') or ev.get('unique_code')
    if old and old != now:
        ev['former_codes'] = ' '.join(dict.fromkeys((ev.get('former_codes') or '').split() + [old]))


def _carry_code(old, new):
    """An event's code changed: its budget, plan line, ballots, funds requests and invoices follow it."""
    if not old or not new or old == new:
        return
    import recode
    import bud_store as B
    url = (B.get(old) or {}).get('files_url', '') if B.enabled() else ''
    moved = recode.move(old, new)
    recode.rename_folder(url, old, new)
    log_audit_event('EVENT_CODE_CHANGED', _signed_in_name(), {'from': old, 'to': new, 'records': moved})
    flash(f'The code is now {new}. Everything in the apps that used {old} has moved to it. If {old} is in a QuickBooks '
          f'project name or on MotorsportReg, change it there too. QuickBooks keeps matching meanwhile, and the Treasurer '
          f'page lists the project until it is renamed.', 'info')


@app.route('/edit/<event_id>/new-code', methods=['POST'])
@login_required
def new_event_code(event_id):
    """Give an event a fresh generated code, for one that reads badly. Its records move with it."""
    with FileLock(DATA_LOCK_FILE):
        events = load_events()
        ev = next((e for e in events if e.get('id') == event_id), None)
        if not ev:
            flash('Event not found.', 'warning')
            return redirect(url_for('bud.home'))
        old = ev.get('custom_tag') or ev.get('unique_code')
        ev['unique_code'], ev['custom_tag'] = generate_unique_code(ev['program_code'], events), ''
        _remember_code(ev, old)
        save_events(events)
        log_audit_event('EVENT_EDITED', _signed_in_name(), {'event_id': event_id, 'new_code': ev['unique_code']})
        _carry_code(old, ev['unique_code'])
    return redirect(url_for('bud.detail', code=ev['unique_code']))

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
