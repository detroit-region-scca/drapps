"""Board minutes: originals and approved PDFs in the private Board Minutes folder (by year), approved PDFs in the public
website folder (by year) that drscca.org lists, and drafts in the DRSCCA Apps drive (MINUTES_DRAFTS_FOLDER_ID), where
the app may delete them. The app's service account can add files to the two Board Minutes folders but not delete."""
import json
import os

import minutes_logic as L

FOLDER = 'application/vnd.google-apps.folder'


def enabled():
    return all(os.environ.get(k) for k in ('MINUTES_PRIVATE_FOLDER_ID', 'MINUTES_PUBLIC_FOLDER_ID', 'MINUTES_DRAFTS_FOLDER_ID'))


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


LIST_SECONDS = 120   # folder listings for the Minutes page; the app's own changes clear them at once


def _listed(parent, q_extra=''):
    """_list for showing pages: shared by every worker for a couple of minutes (Drive is slow to list)."""
    import sheet_cache
    return sheet_cache.remember(f'minutes:{parent}:{q_extra}', LIST_SECONDS, lambda: _list(parent, q_extra))


def _changed():
    """After any upload, replace, publish or delete: the next page lists Drive afresh."""
    import sheet_cache
    sheet_cache.forget('minutes:')


def year_folder(parent, year):
    DRIVE, _ = _drive()
    hit = [f for f in _list(parent, f" and mimeType='{FOLDER}'") if f['name'] == str(year)]
    if hit:
        return hit[0]['id']
    r = _s().post(DRIVE, params={'supportsAllDrives': 'true', 'fields': 'id'},
                  json={'name': str(year), 'mimeType': FOLDER, 'parents': [parent]})
    r.raise_for_status()
    _changed()
    return r.json()['id']


def _upload(parent, name, data, mime, as_google_doc=False):
    _, UPLOAD = _drive()
    meta = {'name': name, 'parents': [parent]}
    if as_google_doc:
        meta['mimeType'] = 'application/vnd.google-apps.document'
    files = {'metadata': ('metadata', json.dumps(meta), 'application/json'), 'file': (name, data, mime)}
    r = _s().post(UPLOAD, params={'uploadType': 'multipart', 'supportsAllDrives': 'true', 'fields': 'id,name,webViewLink'}, files=files)
    r.raise_for_status()
    _changed()
    return r.json()


def _replace_content(file_id, data, mime):
    _, UPLOAD = _drive()
    r = _s().patch(f'{UPLOAD}/{file_id}', params={'uploadType': 'media', 'supportsAllDrives': 'true', 'fields': 'id,name,webViewLink'},
                   data=data, headers={'Content-Type': mime})
    r.raise_for_status()
    _changed()
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
    folder = [f for f in _listed(os.environ['MINUTES_PUBLIC_FOLDER_ID'], f" and mimeType='{FOLDER}'") if f['name'] == str(year)]
    return [f for f in _listed(folder[0]['id'])] if folder else []


def save_draft(name, pdf):
    hit = [f for f in _list(os.environ['MINUTES_DRAFTS_FOLDER_ID']) if f['name'] == name]
    if hit:
        return _replace_content(hit[0]['id'], pdf, L.PDF), True
    return _upload(os.environ['MINUTES_DRAFTS_FOLDER_ID'], name, pdf, L.PDF), False


def drafts():
    return [f for f in _listed(os.environ['MINUTES_DRAFTS_FOLDER_ID']) if f['name'].endswith('.pdf')]


def delete_draft(file_id):
    DRIVE, _ = _drive()
    f = _s().get(f'{DRIVE}/{file_id}', params={'supportsAllDrives': 'true', 'fields': 'id,name,parents'}).json()
    if os.environ['MINUTES_DRAFTS_FOLDER_ID'] not in f.get('parents', []):
        raise ValueError('Not a draft.')
    r = _s().patch(f'{DRIVE}/{file_id}', params={'supportsAllDrives': 'true'}, json={'trashed': True})
    r.raise_for_status()
    _changed()
    return f['name']


def publish_draft(file_id):
    """Put a draft in the public and private Board Minutes folders under its final name, then trash the draft."""
    DRIVE, _ = _drive()
    f = _s().get(f'{DRIVE}/{file_id}', params={'supportsAllDrives': 'true', 'fields': 'id,name'}).json()
    data = _s().get(f'{DRIVE}/{file_id}', params={'alt': 'media', 'supportsAllDrives': 'true'})
    data.raise_for_status()
    final = L.published_name(f['name'])
    when, _ = L.parse(final)
    out, replaced = save(when.year, final, data.content, 'MINUTES_PUBLIC_FOLDER_ID')
    save(when.year, final, data.content, 'MINUTES_PRIVATE_FOLDER_ID')
    delete_draft(file_id)
    return out, replaced


def public_folder_url():
    return f"https://drive.google.com/drive/folders/{os.environ.get('MINUTES_PUBLIC_FOLDER_ID', '')}"


def private_folder_url():
    return f"https://drive.google.com/drive/folders/{os.environ.get('MINUTES_PRIVATE_FOLDER_ID', '')}"
