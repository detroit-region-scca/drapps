import json
import os
import random
import string
import logging
from datetime import datetime
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
    try:
        raw = store.load_events(fresh=not memo)      # a POST may write the list back: read it live
    except TypeError:                                 # the file store has no shared copy to skip
        raw = store.load_events()
    events = sorted(raw, key=lambda x: x.get('date', ''), reverse=False)
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
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading

def _plain_to_html(text):
    """The plain-text body as simple HTML: escaped, URLs and emails linked, paragraphs kept, lines left to reflow."""
    import html as _html
    import re as _re
    out = []
    for para in text.split('\n\n'):
        p = _html.escape(para)
        p = _re.sub(r'(https?://[^\s<]+)', r'<a href="\1">\1</a>', p)
        p = _re.sub(r'(?<![\w/=.@])([\w.+-]+@[\w-]+\.[\w.]+\w)', r'<a href="mailto:\1">\1</a>', p)
        out.append('<p style="margin:0 0 1em">' + p.replace('\n', '<br>') + '</p>')
    return ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.45">'
            + ''.join(out) + '</div>')


def send_mail(subject, body, recipient=None, attachments=(), cc=None, reply_to=None):
    """Send a plain-text email in the background (see the SMTP notes inside).
    attachments: [(filename, bytes, mime type)], e.g. an invoice PDF."""
    recipient = recipient or os.environ.get('NOTIFICATION_EMAIL', 'treasurer@drscca.org')

    def send_email_async():
        smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.environ.get('SMTP_PORT', 587))
        smtp_user = os.environ.get('SMTP_USER')
        smtp_password = os.environ.get('SMTP_PASSWORD')

        # SMTP_USER/SMTP_PASSWORD are optional: a local relay that accepts this host by IP
        # needs none and does the authenticated hop to the mail provider itself.
        if not smtp_server and os.environ.get('MAIL_TRANSPORT') != 'gmail_api':
            logger.error("SMTP_SERVER is not set. Email not sent.")
            return

        if smtp_password:
            smtp_password = smtp_password.replace(" ", "")

        # SMTP_FROM is the explicit knob; without it the login name is used. A relay that
        # needs no credentials has no login, so SMTP_FROM must be set in that case.
        from_email = os.environ.get('SMTP_FROM') or smtp_user
        if not from_email:
            logger.error("SMTP_FROM is not set. Email not sent.")
            return

        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = recipient
        msg['Subject'] = subject
        if reply_to or os.environ.get('SMTP_REPLY_TO'):
            msg['Reply-To'] = reply_to or os.environ['SMTP_REPLY_TO']    # replies go to the shared inbox (or the board)
        if cc:
            msg['Cc'] = cc
        footer = os.environ.get('MAIL_FOOTER', 'This is an automated message from the DRSCCA apps. Replies to this address are not read: '
                                 'use the links above, or write to your program group or admin@drscca.org.')
        text = f"{body}\n\n--\n{footer}" if footer else body
        # Plain text alone gets hard-wrapped by some mail apps (Gmail on Android breaks long lines mid-sentence),
        # so send an HTML copy too: it reflows to the screen. Same words, links made clickable.
        alt = MIMEMultipart('alternative')
        alt.attach(MIMEText(text, 'plain'))
        alt.attach(MIMEText(_plain_to_html(text), 'html'))
        msg.attach(alt)
        for name, data, mime in attachments:
            main, _, sub = (mime or 'application/octet-stream').partition('/')
            part = MIMEBase(main, sub)
            part.set_payload(data)
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment', filename=name)
            msg.attach(part)

        if os.environ.get('MAIL_TRANSPORT') == 'gmail_api':
            # Send through the Gmail API as the region's service account (domain-wide delegation, gmail.send only):
            # no password on this server and no dependence on its IP address. GMAIL_SENDER is the Workspace mailbox
            # it sends from; SMTP_FROM (e.g. apps@) must be a verified send-as address of that mailbox.
            try:
                import base64
                from google.auth.transport.requests import AuthorizedSession
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_file(
                    os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], scopes=['https://www.googleapis.com/auth/gmail.send'],
                    subject=os.environ['GMAIL_SENDER'])
                raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
                r = AuthorizedSession(creds).post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                                                  json={'raw': raw}, timeout=60)
                r.raise_for_status()
                logger.info(f"Email sent to {recipient}: {subject}")
            except Exception as e:
                logger.error(f"Failed to send email: {e}")
            return

        try:
            server = smtplib.SMTP(smtp_server, smtp_port)
            # Only negotiate TLS and authenticate when a credential is actually
            # configured, so pointing SMTP_SERVER straight at an authenticating
            # provider keeps working exactly as before. Against a local relay both
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


def to_number(v):
    """float() for form input that refuses "nan" and "inf" (float() accepts both), so they can't slip past checks."""
    import math
    x = float(v)
    if not math.isfinite(x):
        raise ValueError(f'not a number: {v!r}')
    return x


def local_path(target):
    """target if it is a path on this site ("/x"), else ''. Refuses "//host" and "/\\host", which browsers treat as
    another site, so a crafted link can't bounce someone off to it after a form. Also refuses any tab, line break or
    other control character: browsers drop those from a link, which turns "/<tab>/host" into "//host"."""
    t = str(target or '')
    if not t.startswith('/') or t.startswith(('//', '/\\')) or any(ord(ch) < 32 or ord(ch) == 127 for ch in t):
        return ''
    return t


# ---------- QuickBooks refresh on request (the host's drscca-qbo-refresh.path runs the sync when the request file changes) ----------
QBO_REQUEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'qbo-refresh.json')
QBO_LAST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'qbo-last-run.json')


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def qbo_state():
    """{'requested': iso or '', 'last': iso or '', 'pending': bool}: whether a refresh asked for has not finished yet."""
    req, last = _read_json(QBO_REQUEST).get('at', ''), _read_json(QBO_LAST).get('at', '')
    return {'requested': req, 'last': last, 'pending': bool(req) and req > last}


def request_qbo_refresh(who):
    """Ask the host to sync QuickBooks now. Returns False when one is already waiting (asked in the last 10 minutes)."""
    from datetime import datetime, timedelta
    s = qbo_state()
    now = datetime.now().astimezone()
    if s['pending'] and s['requested'] > (now - timedelta(minutes=10)).isoformat(timespec='seconds'):
        return False
    tmp = QBO_REQUEST + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({'at': now.isoformat(timespec='seconds'), 'by': who}, f)
    os.replace(tmp, QBO_REQUEST)
    return True


# ---------- changes to the officials list, made by the admin in the app and applied on the server ----------
OFFICIALS_QUEUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'officials-queue.json')
OFFICIALS_DONE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'officials-done.json')


def queue_official_change(change):
    from datetime import datetime
    from filelock import FileLock
    with FileLock(OFFICIALS_QUEUE + '.lock'):
        q = _read_json(OFFICIALS_QUEUE) or {'changes': []}
        q['changes'].append(dict(change, at=datetime.now().astimezone().isoformat(timespec='seconds')))
        tmp = OFFICIALS_QUEUE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(q, f, indent=1)
        os.replace(tmp, OFFICIALS_QUEUE)


def official_changes():
    """{'pending': [...], 'done': [last few results]} for the Officials page."""
    return {'pending': (_read_json(OFFICIALS_QUEUE) or {}).get('changes', []),
            'done': ((_read_json(OFFICIALS_DONE) or {}).get('results', []))[-5:][::-1]}
