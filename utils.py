import json
import os
import random
import string
import logging
from datetime import datetime
from filelock import FileLock
import pytz
from functools import wraps
from flask import g, has_request_context, session, redirect, url_for, request

logger = logging.getLogger(__name__)

# Core paths and config. Storage itself (files or Google Sheets) lives in storage.py;
# the paths are re-exported because app.py imports them for its lock files.
from storage import store, DATA_DIR, DATA_FILE, AUDIT_FILE, DATA_LOCK_FILE, PROGRAMS_FILE  # noqa: F401
TIMEZONE = os.environ.get("TIMEZONE", "US/Eastern")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('home', next=request.full_path.rstrip('?')))
        return f(*args, **kwargs)
    return decorated_function

def load_programs():
    return store.load_programs()

def load_events():
    """All events, oldest first. A page view reads the sheet once however many times it asks (the Annual Plan asks
    once per program, which ran into the Sheets read quota); a POST always reads fresh, since it may write the
    list back. Callers get their own copy of the list, so changing it never leaks into the next caller."""
    memo = has_request_context() and request.method == 'GET'
    if memo and '_events' in g:
        return [dict(e) for e in g._events]
    events = sorted(store.load_events(), key=lambda x: x.get('date', ''), reverse=False)
    if memo:
        g._events = [dict(e) for e in events]
    return events

def save_events(events):
    store.save_events(events)

def log_audit_event(action, user, details):
    try:
        tz = pytz.timezone(TIMEZONE)
    except pytz.UnknownTimeZoneError:
        tz = pytz.UTC
        
    log_entry = {
        "timestamp": datetime.now(tz).isoformat(),
        "action": action,
        "user": user,
        "details": details
    }
    store.append_audit(log_entry)

def load_audit_logs():
    logs = store.load_audit()
    return sorted(logs, key=lambda x: x.get('timestamp', ''), reverse=True)

def format_custom_tag(tag):
    if not tag:
        return ""
    tag = tag.strip().upper()
    return f"#{tag}" if not tag.startswith('#') else tag

def is_tag_unique(tag, existing_events, exclude_event_id=None):
    if not tag:
        return True
    return not any(
        tag in (e.get("unique_code"), e.get("custom_tag"))
        for e in existing_events if e.get("id") != exclude_event_id
    )

def generate_unique_code(program_code, existing_events):
    event_code_prefix = os.environ.get("EVENT_CODE_PREFIX", "SCCA")
    existing_tags = {tag for e in existing_events for tag in (e.get("unique_code"), e.get("custom_tag")) if tag}
    
    k = 3
    attempts = 0
    while True:
        suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=k))
        code = f"#{event_code_prefix}{suffix}"
        if code not in existing_tags:
            return code
        attempts += 1
        if attempts > 100:
            k += 1
            attempts = 0

def generate_sequence_id(program_type, date_str, existing_events):
    try:
        year_prefix = str(datetime.strptime(date_str, '%Y-%m-%d').year)
    except ValueError:
        year_prefix = str(datetime.now().year)

    max_seq = 0
    for e in existing_events:
        if e.get('program_code') == program_type and e.get('date', '').startswith(year_prefix):
            try:
                parts = e.get('sequence_id', '').split('-')
                if len(parts) == 3 and parts[0] == year_prefix and parts[2] == program_type:
                    num = int(parts[1])
                    if num > max_seq:
                        max_seq = num
            except (ValueError, IndexError):
                continue
                
    return f"{year_prefix}-{max_seq + 1:02d}-{program_type}"
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading

def send_mail(subject, body, recipient=None):
    """Send a plain-text email in the background (see the SMTP notes inside)."""
    recipient = recipient or os.environ.get('NOTIFICATION_EMAIL', 'treasurer@drscca.org')

    def send_email_async():
        smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.environ.get('SMTP_PORT', 587))
        smtp_user = os.environ.get('SMTP_USER')
        smtp_password = os.environ.get('SMTP_PASSWORD')

        # SMTP_USER/SMTP_PASSWORD are optional. The lab relay on 192.168.4.114:25
        # takes no credentials at all: it accepts this host by IP via postfix
        # `mynetworks` and does the authenticated hop to Purelymail itself, so the
        # provider password lives in exactly one place instead of in every app.
        if not smtp_server:
            logger.error("SMTP_SERVER is not set. Email not sent.")
            return

        if smtp_password:
            smtp_password = smtp_password.replace(" ", "")

        # From used to be smtp_user, which is empty when relaying without
        # credentials. SMTP_FROM is the explicit knob; the relay rewrites every
        # sender to service@cosmoslab.dev anyway, so this only affects the header.
        from_email = os.environ.get('SMTP_FROM') or smtp_user or 'service@cosmoslab.dev'

        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        try:
            server = smtplib.SMTP(smtp_server, smtp_port)
            # Only negotiate TLS and authenticate when a credential is actually
            # configured, so pointing SMTP_SERVER straight at an authenticating
            # provider keeps working exactly as before. Against the lab relay both
            # steps must be skipped: it presents the stock self-signed snakeoil
            # certificate, which starttls() rejects on modern Python, and it has no
            # SASL backend, so AUTH returns 503.
            if smtp_user and smtp_password:
                server.starttls()
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
            server.quit()
            logger.info(f"Email sent to {recipient}: {subject}")
        except Exception as e:
            logger.error(f"Failed to send email: {e}")

    threading.Thread(target=send_email_async).start()


def send_new_event_email(event):
    year = event.get('date', '')[:4]
    tag = event.get('custom_tag') or event.get('unique_code')
    full_event_string = f"{year} {event.get('program_code')} {event.get('event_name')} {tag}"
    send_mail(f"New Event Registered: {event.get('event_name')}", f"""A new event has been registered in the DRSCCA system.

Full Event String: {full_event_string}

Event Details:
- Name: {event.get('event_name')}
- Date: {event.get('date')}
- Program: {event.get('program_code')}
- Unique Code: {tag}
- Created By: {event.get('creator_name')}

Please review this entry if needed.
""")
