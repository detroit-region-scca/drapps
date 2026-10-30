"""Which documents the region has for each vehicle, trailer and storage place, and which are missing. Each row of the
Documents tab names a Drive folder and the documents it should hold ("Title, Registration, Insurance card"); a document
counts as there when a file in that folder has its name in the file name ("Title - front.jpg"). The folders are plain
Drive folders in Operations/Documents, so people can add files there directly, without the app.
"""
import re
import time

from storage import DRIVE, sheets_client

CACHE_SECONDS = 300
# A few other words that mean the same document.
ALSO = {'agreement': r'agreement|lease|contract', 'insurance card': r'insurance card|proof of insurance|no-fault certificate',
        'registration': r'registration|license tab', 'policy': r'policy|declaration'}
_cache = {}


def folder_id(url):
    m = re.search(r'/folders/([\w-]+)', url or '')
    return m.group(1) if m else None


def _files(ids):
    """{folder id: [{'name', 'webViewLink', 'modifiedTime'}]}, one Drive query for all folders, cached a few minutes."""
    key = tuple(sorted(ids))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    out = {f: [] for f in ids}
    session = sheets_client().http_client.session
    for n in range(0, len(key), 40):
        q = '(' + ' or '.join(f"'{f}' in parents" for f in key[n:n + 40]) + ') and trashed = false'
        token = None
        while True:
            r = session.get(DRIVE, params={'q': q, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true', 'corpora': 'allDrives',
                                           'pageSize': 1000, 'pageToken': token or '',
                                           'fields': 'nextPageToken,files(name,webViewLink,modifiedTime,parents,mimeType)'})
            r.raise_for_status()
            body = r.json()
            for f in body.get('files', []):
                for parent in f.get('parents', []):
                    if parent in out:
                        out[parent].append(f)
            token = body.get('nextPageToken')
            if not token:
                break
    _cache[key] = (time.time(), out)
    return out


def refresh():
    _cache.clear()


def needed(row):
    return [n.strip() for n in (row.get('needed') or '').split(',') if n.strip()]


def matches(doc, filename):
    pattern = ALSO.get(doc.lower(), re.escape(doc))
    return re.search(pattern, filename, re.I) is not None


def status(rows):
    """Each Documents row with 'have' [(doc, [files])], 'missing' [doc], 'others' [files] and 'error' if Drive could not
    be read."""
    ids = [fid for fid in (folder_id(r['folder']) for r in rows) if fid]
    try:
        files, error = _files(ids), ''
    except Exception as e:      # the page still shows what is needed, just not what is there
        files, error = {}, str(e)
    out = []
    for r in rows:
        fs = sorted(files.get(folder_id(r['folder']), []), key=lambda f: f['name'].lower())
        fs = [f for f in fs if f.get('mimeType') != 'application/vnd.google-apps.folder']
        have = [(d, [f for f in fs if matches(d, f['name'])]) for d in needed(r)]
        used = {f['name'] for _, m in have for f in m}
        out.append(dict(r, have=[(d, m) for d, m in have if m], missing=[d for d, m in have if not m] if not error else [],
                        others=[f for f in fs if f['name'] not in used], error=error))
    return out
