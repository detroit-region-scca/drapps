"""Board minutes in the Region shared drive: originals and drafts in the private Board Minutes folder (by year), the
approved PDFs in the public website folder (by year) that drscca.org lists. The app's service account has writer
access to just those two folders."""
import io
import json
import os

import minutes_logic as L

FOLDER = 'application/vnd.google-apps.folder'


def enabled():
    return bool(os.environ.get('MINUTES_PRIVATE_FOLDER_ID') and os.environ.get('MINUTES_PUBLIC_FOLDER_ID'))


def _s():
    from storage import sheets_client
    return sheets_client().http_client.session


def _drive():
    from storage import DRIVE, UPLOAD
    return DRIVE, UPLOAD


def _list(parent, q_extra=''):
    DRIVE, _ = _drive()
    r = _s().get(DRIVE, params={'q': f"'{parent}' in parents and trashed=false{q_extra}", 'supportsAllDrives': 'true',
                                'includeItemsFromAllDrives': 'true', 'pageSize': 1000,
                                'fields': 'files(id,name,mimeType,webViewLink,modifiedTime)'})
    r.raise_for_status()
    return r.json().get('files', [])


def year_folder(parent, year):
    DRIVE, _ = _drive()
    hit = [f for f in _list(parent, f" and mimeType='{FOLDER}'") if f['name'] == str(year)]
    if hit:
        return hit[0]['id']
    r = _s().post(DRIVE, params={'supportsAllDrives': 'true', 'fields': 'id'},
                  json={'name': str(year), 'mimeType': FOLDER, 'parents': [parent]})
    r.raise_for_status()
    return r.json()['id']


def _upload(parent, name, data, mime, as_google_doc=False):
    _, UPLOAD = _drive()
    meta = {'name': name, 'parents': [parent]}
    if as_google_doc:
        meta['mimeType'] = 'application/vnd.google-apps.document'
    files = {'metadata': ('metadata', json.dumps(meta), 'application/json'), 'file': (name, data, mime)}
    r = _s().post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id,name,webViewLink'}, files=files)
    r.raise_for_status()
    return r.json()


def _replace_content(file_id, data, mime):
    _, UPLOAD = _drive()
    r = _s().patch(f'{UPLOAD}/{file_id}', params={'uploadType': 'media', 'supportsAllDrives': 'true', 'fields': 'id,name,webViewLink'},
                   data=data, headers={'Content-Type': mime})
    r.raise_for_status()
    return r.json()


def to_pdf(data, upload_name, scratch_parent):
    """PDF bytes for an upload: PDFs as they are; Word and other documents converted by Google Drive."""
    if L.kind_of_upload(upload_name) == 'pdf':
        return data
    DRIVE, _ = _drive()
    doc = _upload(scratch_parent, 'converting minutes (temporary)', data, L.mime_for(upload_name), as_google_doc=True)
    try:
        r = _s().get(f"{DRIVE}/{doc['id']}/export", params={'mimeType': L.PDF})
        r.raise_for_status()
        return r.content
    finally:
        _s().patch(f"{DRIVE}/{doc['id']}", params={'supportsAllDrives': 'true'}, json={'trashed': True})


def save(year, name, pdf, folder_env):
    """Put the PDF in <folder>/<year>/ under this name; an existing file of that name gets the new content (its link
    stays the same). Returns (file, replaced)."""
    folder = year_folder(os.environ[folder_env], year)
    hit = [f for f in _list(folder) if f['name'] == name]
    if hit:
        return _replace_content(hit[0]['id'], pdf, L.PDF), True
    return _upload(folder, name, pdf, L.PDF), False


def keep_original(year, name, data, mime):
    return _upload(year_folder(os.environ['MINUTES_PRIVATE_FOLDER_ID'], year), name, data, mime)


def published(year):
    folder = [f for f in _list(os.environ['MINUTES_PUBLIC_FOLDER_ID'], f" and mimeType='{FOLDER}'") if f['name'] == str(year)]
    return [f for f in _list(folder[0]['id'])] if folder else []


def drafts(years):
    out = []
    for y in years:
        folder = [f for f in _list(os.environ['MINUTES_PRIVATE_FOLDER_ID'], f" and mimeType='{FOLDER}'") if f['name'] == str(y)]
        if folder:
            out += [dict(f, year=y) for f in _list(folder[0]['id']) if '(draft)' in f['name']]
    return out


def publish_draft(file_id, year):
    """Copy a draft PDF to the public folder under its final name, and drop "(draft)" from the private copy."""
    DRIVE, _ = _drive()
    f = _s().get(f'{DRIVE}/{file_id}', params={'supportsAllDrives': 'true', 'fields': 'id,name'}).json()
    data = _s().get(f'{DRIVE}/{file_id}', params={'alt': 'media', 'supportsAllDrives': 'true'})
    data.raise_for_status()
    final = L.published_name(f['name'])
    out, replaced = save(year, final, data.content, 'MINUTES_PUBLIC_FOLDER_ID')
    _s().patch(f'{DRIVE}/{file_id}', params={'supportsAllDrives': 'true'}, json={'name': final})
    return out, replaced


def public_folder_url():
    return f"https://drive.google.com/drive/folders/{os.environ.get('MINUTES_PUBLIC_FOLDER_ID', '')}"


def private_folder_url():
    return f"https://drive.google.com/drive/folders/{os.environ.get('MINUTES_PRIVATE_FOLDER_ID', '')}"
