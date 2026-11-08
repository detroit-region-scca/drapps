"""Which documents the region has for each vehicle, trailer and storage place, and which are missing. Each row of the
Documents tab names a Drive folder and the documents it should hold ("Title, Registration, Insurance card"); a document
counts as there when a file in that folder has its name in the file name ("Title - front.jpg"). The folders are plain
Drive folders in the Operations folder (vehicles and trailers in "Fleet Titles & Registrations", storage places in
"Storage Locations"), so people can add files there directly, without the app.
"""
import re

from storage import DRIVE, sheets_client

CACHE_SECONDS = 300
# A few other words that mean the same document.
ALSO = {'agreement': r'agreement|lease|contract', 'insurance card': r'insurance card|proof of insurance|no-fault certificate',
        'registration': r'registration|license tab', 'policy': r'policy|declaration'}
_cache = {}


# Where a new item's folder goes, inside the Operations folder (OPERATIONS_DOCS_FOLDER_URL).
SECTION = {'EQ': 'Fleet Titles & Registrations', 'PL': 'Storage Locations'}


def section_folder(root, rid):
    """The id of the Operations subfolder a vehicle (EQ-) or storage place (PL-) belongs in, made if missing."""
    name = SECTION[rid[:2]]
    session = sheets_client().http_client.session
    q = f"'{root}' in parents and name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    r = session.get(DRIVE, params={'q': q, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true', 'corpora': 'allDrives',
                                   'fields': 'files(id)'})
    r.raise_for_status()
    hit = r.json().get('files', [])
    if hit:
        return hit[0]['id']
    r = session.post(DRIVE, params={'supportsAllDrives': 'true', 'fields': 'id'},
                     json={'name': name, 'mimeType': 'application/vnd.google-apps.folder', 'parents': [root]})
    r.raise_for_status()
    return r.json()['id']


def folder_id(url):
    m = re.search(r'/folders/([\w-]+)', url or '')
    return m.group(1) if m else None


def _files(ids):
    """{folder id: [{'name', 'webViewLink', 'modifiedTime'}]}, one Drive query for all folders, cached a few minutes."""
    key = tuple(sorted(ids))
    import sheet_cache
    return sheet_cache.remember('docs:' + ','.join(key), CACHE_SECONDS, lambda: _list(key))


def _list(key):
    out = {f: [] for f in key}
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
    return out


def refresh():
    import sheet_cache
    _cache.clear()
    sheet_cache.forget('docs:')


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


# What a new vehicle, trailer or storage place should have on file, when its folder is made by its first upload.
DEFAULT_NEEDED = {'Vehicle': 'Title, Registration, Insurance card', 'Trailer': 'Title, Registration', 'place': 'Agreement'}
# Offered in the upload form's "What is it?" list after the ones the row needs.
KINDS = ['Title', 'Registration', 'Insurance card', 'Bill of sale', 'Agreement', 'Policy', 'Receipt', 'Photo']
ACCEPT = 'image/*,.pdf,.heic,.doc,.docx'


def kinds_for(row):
    """The "What is it?" choices: missing documents first, then the rest of what this row needs, then common ones."""
    need = needed(row or {})
    missing = (row or {}).get('missing') or need
    out = missing + [d for d in need if d not in missing]
    return out + [k for k in KINDS if k.lower() not in {d.lower() for d in out}]


def _clean(s):
    return re.sub(r'\s+', ' ', re.sub(r'[\\/:*?"<>|]+', ' ', s or '')).strip()


def file_name(doc, for_name, note, original, n=1):
    """"Title - 2003 Gulf Stream motorhome - 2026.jpg": the document's name first, so it matches what the row needs."""
    ext = ''
    m = re.search(r'\.([A-Za-z0-9]{1,5})$', original or '')
    if m:
        ext = '.' + m.group(1).lower()
    parts = [_clean(doc), _clean(for_name)] + ([_clean(note)] if _clean(note) else [])
    return ' - '.join(p for p in parts if p) + (f' ({n})' if n > 1 else '') + ext


def upload(folder, name, f, who):
    """Put one uploaded file (Werkzeug FileStorage) into a documents folder under the given name."""
    import json
    from storage import UPLOAD
    meta = {'name': name, 'parents': [folder], 'description': f'Uploaded in DRSCCA Apps by {who}'}
    files = {'metadata': ('metadata', json.dumps(meta), 'application/json'),
             'file': (name, f.stream, f.mimetype or 'application/octet-stream')}
    r = sheets_client().http_client.session.post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true',
                                                                  'fields': 'id,name,webViewLink'}, files=files)
    r.raise_for_status()
    return r.json()
