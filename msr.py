"""MotorsportReg: what an event's public MotorsportReg page says (date, cancelled), to compare with the app.

A person pastes the event's MotorsportReg link on the event page; nothing is matched automatically. The page is
read when the event page is opened, at most every few hours (data/msr-cache.json); the events list only reads that
copy, so it never waits on MotorsportReg.
"""
import html
import json
import os
import re
import time
import urllib.request
from datetime import datetime

from filelock import FileLock

from storage import DATA_DIR

CACHE = os.path.join(DATA_DIR, 'msr-cache.json')
LOCK = CACHE + '.lock'
TTL = 6 * 3600
URL = re.compile(r'^https://(www\.)?motorsportreg\.com/events/[A-Za-z0-9-]+/?$')
DATE = re.compile(r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*, ((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, 20\d\d)')


def clean_url(text):
    """A pasted MotorsportReg event link, without tracking bits, or '' if it is not one."""
    u = (text or '').strip().split('?')[0].split('#')[0].replace('http://', 'https://')
    return u.rstrip('/') if URL.match(u) else ''


def parse(page):
    """{'title', 'date' (YYYY-MM-DD, the first day), 'cancelled'} from an event page's HTML."""
    m = re.search(r'<title>(.*?)</title>', page, re.S)
    title = html.unescape(m.group(1)).strip() if m else ''
    text = html.unescape(re.sub(r'<[^>]+>', ' ', re.sub(r'<script.*?</script>|<style.*?</style>', '', page, flags=re.S)))
    d = DATE.search(re.sub(r'\s+', ' ', text))
    iso = ''
    if d:
        for fmt in ('%b %d, %Y', '%B %d, %Y'):
            try:
                iso = datetime.strptime(d.group(1), fmt).date().isoformat()
                break
            except ValueError:
                pass
    title = re.sub(r'\s*[|:-]\s*MotorsportReg.*$', '', title, flags=re.I)
    return {'title': title, 'date': iso, 'cancelled': bool(re.search(r'cancel', title, re.I))}


def _load():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def cached(url):
    """What we last read for this link, or None (never fetches)."""
    return _load().get(url) if url else None


def check(url, fetch=None):
    """Read the page if our copy is older than TTL; returns the info (with 'checked') or the old copy, or None."""
    if not url:
        return None
    old = cached(url)
    if old and time.time() - old.get('checked', 0) < TTL:
        return old
    try:
        if fetch:
            page = fetch(url)
        else:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (DRSCCA Apps)'})
            with urllib.request.urlopen(req, timeout=8) as r:
                page = r.read().decode('utf-8', 'replace')
        info = dict(parse(page), checked=time.time())
    except Exception:
        return old
    with FileLock(LOCK):
        data = _load()
        data[url] = info
        tmp = CACHE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, CACHE)
    return info


def differences(e, info):
    """Plain reasons the app and MotorsportReg disagree, for the event page and the list's warning sign."""
    if not info:
        return []
    out = []
    if info.get('cancelled') and not e.get('cancelled'):
        out.append('MotorsportReg says this event is cancelled')
    if info.get('date') and info['date'] != str(e.get('date', ''))[:10]:
        out.append(f"MotorsportReg has it on {datetime.fromisoformat(info['date']).strftime('%b %-d, %Y')}")
    return out
