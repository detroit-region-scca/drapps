"""Weekly copy of the drscca.org website's content to the region shared drive (drscca-site-backup.timer).

The site is on WordPress.com Premium, which has no do-it-yourself backups, so the server keeps its own: every page,
post, category and tag, the list of media files (with their addresses), and the home page as a visitor sees it,
read from the site's public WordPress API. Only published, public content is read, so no login is needed.

Each week's copy is one .tar.gz of JSON files in "Website Archive/Weekly site backups" in the DRSCCA Region drive;
the newest 8 are kept and older ones go to the Drive trash. To rebuild the site, the pages and posts can be pasted
back from these files; the media files are listed with their original addresses. A full WordPress export can also be
made by hand in wp-admin > Tools > Export.
"""
import io
import json
import os
import sys
import tarfile
import time
from datetime import date

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

SITE = 'https://drscca.org'
SA = os.environ.get('WORKSPACE_SA', '/opt/drscca/data/secrets/workspace-sa.json')
REGION_DRIVE = '0AOGLnEtgl5aCUk9PVA'
PARENT = 'Website Archive'
FOLDER = 'Weekly site backups'
KEEP = 8
API = 'https://www.googleapis.com/drive/v3/files'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
P = {'supportsAllDrives': 'true'}
UA = {'User-Agent': 'DRSCCA site backup (drapps.drscca.org)'}


def everything(kind):
    """Every item of one public REST collection, 100 at a time."""
    out, page = [], 1
    while True:
        for attempt in range(3):
            r = requests.get(f'{SITE}/wp-json/wp/v2/{kind}', params={'per_page': 100, 'page': page}, headers=UA, timeout=60)
            if r.status_code < 500:
                break
            time.sleep(10)
        if r.status_code == 400 and page > 1:      # past the last page
            return out
        r.raise_for_status()
        items = r.json()
        out += items
        if page >= int(r.headers.get('X-WP-TotalPages', page)) or not items:
            return out
        page += 1


def main():
    parts = {k: everything(k) for k in ('pages', 'posts', 'categories', 'tags', 'media')}
    if len(parts['pages']) < 10 or len(parts['posts']) < 10:
        sys.exit(f"Too little came back (pages {len(parts['pages'])}, posts {len(parts['posts'])}); not replacing a good backup.")
    home = requests.get(SITE + '/', headers=UA, timeout=60).text

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as t:
        def add(name, text):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mtime = len(data), int(time.time())
            t.addfile(info, io.BytesIO(data))
        for k, v in parts.items():
            add(f'{k}.json', json.dumps(v, indent=1))
        add('home.html', home)
        add('README.txt', __doc__)
    body = buf.getvalue()

    cr = service_account.Credentials.from_service_account_file(
        SA, scopes=['https://www.googleapis.com/auth/drive']).with_subject('admin@drscca.org')
    s = AuthorizedSession(cr)
    find = dict(P, includeItemsFromAllDrives='true', corpora='drive', driveId=REGION_DRIVE, fields='files(id,name)')

    def folder(name, parent):
        q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false" + \
            (f" and '{parent}' in parents" if parent else '')
        got = s.get(API, params=dict(find, q=q)).json()['files']
        if got:
            return got[0]['id']
        r = s.post(API, params=dict(P, fields='id'), json={'name': name, 'mimeType': 'application/vnd.google-apps.folder',
                                                         'parents': [parent or REGION_DRIVE]})
        r.raise_for_status()
        return r.json()['id']

    dest = folder(FOLDER, folder(PARENT, None))
    name = f'drscca.org site backup {date.today().isoformat()}.tar.gz'
    meta = json.dumps({'name': name, 'parents': [dest]})
    r = s.post(UPLOAD, params=dict(P, uploadType='multipart', fields='id'), files={
        'metadata': ('metadata', meta, 'application/json; charset=UTF-8'), 'file': (name, body, 'application/gzip')})
    r.raise_for_status()

    old = s.get(API, params=dict(find, q=f"'{dest}' in parents and trashed = false", orderBy='name desc')).json()['files']
    for f in [f for f in old if f['name'].startswith('drscca.org site backup ')][KEEP:]:
        s.patch(f"{API}/{f['id']}", params=P, json={'trashed': True}).raise_for_status()
    print(f"{name}: {len(body)} bytes; pages {len(parts['pages'])}, posts {len(parts['posts'])}, media {len(parts['media'])}")


if __name__ == '__main__':
    main()
