"""Short links for emails: https://drapps.drscca.org/l/<id> stands in for a long signed link (a vote, an approval).

The email shows only the short link. Opening it redirects to the full signed link, which still does all the checking
(signature, who it is for, how old it is), so a short link can never do more than the link behind it. Ids are random,
so they cannot be guessed. Kept in data/short_links.json; entries are dropped once the link behind them has expired.
"""
import json
import os
import secrets
import time

from filelock import FileLock
from flask import Blueprint, abort, redirect, request, url_for

from storage import DATA_DIR

PATH = os.path.join(DATA_DIR, 'short_links.json')
LOCK = PATH + '.lock'
bp = Blueprint('short', __name__)


def _load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def make(url, days):
    """A short link to this url, valid for as many days as the link behind it."""
    now = time.time()
    with FileLock(LOCK):
        links = {k: v for k, v in _load().items() if v.get('expires', 0) > now}
        sid = secrets.token_urlsafe(8)
        while sid in links:
            sid = secrets.token_urlsafe(8)
        links[sid] = {'url': url, 'expires': now + days * 86400}
        tmp = PATH + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(links, f)
        os.replace(tmp, PATH)
    return f"{request.url_root.rstrip('/')}{url_for('short.go', sid=sid)}"


@bp.route('/l/<sid>')
def go(sid):
    link = _load().get(sid)
    if not link or link.get('expires', 0) < time.time():
        abort(404)
    return redirect(link['url'])
