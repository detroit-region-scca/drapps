"""MotorsportReg: what an event's public MotorsportReg page says (date, cancelled), to compare with the app.

The region's convention ties the two: a director puts the event's #DR code in its MotorsportReg name
("DRSCCA Solo #6: Fall into Autumn #DRR7O"). The app finds that event on the region's MotorsportReg pages
(MSR_ORGS). Where the name cannot carry our code (an event on another club's page), a person pastes the link on
the event page instead. Pages are read at most every few hours (data/msr-cache.json); the events list only reads
that copy, so it never waits on MotorsportReg.
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
ORGS = [u.strip() for u in os.environ.get('MSR_ORGS', 'https://www.motorsportreg.com/orgs/scca/detroit').split(',') if u.strip()]
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


def _get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (DRSCCA Apps)'})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode('utf-8', 'replace')


def _save(key, value):
    with FileLock(LOCK):
        data = _load()
        data[key] = value
        tmp = CACHE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, CACHE)


def parse_org(page):
    """[(event link, name)] from an organization's MotorsportReg page."""
    out = []
    for m in re.finditer(r'<a[^>]+href="(/events/[^"?#]+)"[^>]*>(.*?)</a>', page, re.S):
        name = html.unescape(re.sub(r'<[^>]+>|\s+', ' ', m.group(2))).strip()
        if name:
            out.append(('https://www.motorsportreg.com' + m.group(1).rstrip('/'), name))
    return out


def org_index(fetch=None, refresh=True):
    """Every event on the region's MotorsportReg pages as [(link, name)], read at most every TTL."""
    old = _load().get('_orgs') or {}
    if not refresh or time.time() - old.get('checked', 0) < TTL:
        return old.get('events', [])
    events = []
    try:
        for org in ORGS:
            events += parse_org((fetch or _get)(org))
    except Exception:
        return old.get('events', [])
    _save('_orgs', {'checked': time.time(), 'events': events})
    return events


def find(code, index):
    """The MotorsportReg link whose name carries this event code, or ''. Several (an event split into competitor
    and volunteer entries): the first that is not a volunteer/worker entry."""
    if not code:
        return ''
    pat = re.compile(re.escape(code) + r'(?![A-Za-z0-9])', re.I)
    hits = [(u, n) for u, n in index if pat.search(n)]
    hits.sort(key=lambda h: bool(re.search(r'volunteer|worker', h[1], re.I)))
    return hits[0][0] if hits else ''


def link_for(e, code, refresh=False):
    """(link, how): how is 'name' (found by the code in its MotorsportReg name), 'pasted', or ''."""
    hit = find(code, org_index(refresh=refresh))
    if hit:
        return hit, 'name'
    if e.get('msr_url'):
        return e['msr_url'], 'pasted'
    return '', ''


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
        info = dict(parse((fetch or _get)(url)), checked=time.time())
    except Exception:
        return old
    _save(url, info)
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
