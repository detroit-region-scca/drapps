"""Forged-form protection: every POST from a browser must carry the session's token.

The token lives in the session and is added to every <form method="post"> on the way out (after_request), so the
templates need nothing. Requests signed in with an API key (g.api_user; the MCP server and scripts) are exempt: a
browser on another site cannot add that key, which is what makes them safe without a token."""
import hmac
import re
import secrets

from flask import flash, g, redirect, request, session, url_for
from markupsafe import escape

FIELD = '_csrf'
_FORM = re.compile(r'(<form\b[^>]*\bmethod\s*=\s*["\']?post["\']?[^>]*>)', re.IGNORECASE)


def token():
    t = session.get(FIELD)
    if not t:
        t = session[FIELD] = secrets.token_urlsafe(32)
    return t


def check():
    """before_request: refuse a browser POST without the right token."""
    if request.method != 'POST' or getattr(g, 'api_user', None):
        return None
    sent = request.form.get(FIELD) or request.headers.get('X-CSRF-Token') or ''
    want = session.get(FIELD) or ''
    if want and hmac.compare_digest(sent, want):
        return None
    flash('That form had expired. Please try again.', 'warning')
    back = request.referrer or ''
    return redirect(back if back.startswith(request.host_url) else url_for('home'))


def stamp(resp):
    """after_request: put the token into every POST form of an HTML page."""
    if resp.mimetype != 'text/html' or resp.direct_passthrough or resp.status_code >= 300:
        return resp
    html = resp.get_data(as_text=True)
    if '<form' not in html.lower():
        return resp
    field = f'<input type="hidden" name="{FIELD}" value="{escape(token())}">'
    new = _FORM.sub(lambda m: m.group(1) + field, html)
    if new != html:
        resp.set_data(new)
    return resp
