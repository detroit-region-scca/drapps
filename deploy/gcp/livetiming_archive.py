"""Nightly live timing archive (drapps VM, run as root by drscca-livetiming-archive.timer).

1. For each program, the pages from its latest event are copied to www/archive/<program>/<date>/ (date of the last
   upload, Detroit time). A later upload the same day replaces that day's copy. Pages for classes that did not
   run that day are still there from an earlier event; they are left out.
2. www/archive/index.json lists every archived event, newest first; the live timing front page shows it.
3. Each archived event not yet in Drive is copied to "Timing and Results/Live timing pages/<Program>/<date>" in the region shared drive
   (as admin@ through the workspace service account). Nothing is ever deleted, here or in Drive.
"""
import json
import os
import shutil
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

WWW = '/opt/drscca/data/livetiming/www'
ARCHIVE = os.path.join(WWW, 'archive')
PROGRAMS = {'autocross': 'Autocross', 'rallycross': 'RallyCross'}
TZ = ZoneInfo('America/Detroit')
REGION_DRIVE = '0AOGLnEtgl5aCUk9PVA'
DRIVE_PATH = ('Timing and Results', 'Live timing pages')
SA = '/opt/drscca/data/secrets/workspace-sa.json'
API = 'https://www.googleapis.com/drive/v3/files'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
STAMP = '.in-drive'
FRESH = 16 * 3600       # pages older than this before the newest upload belong to an earlier event


def newest(folder):
    times = [os.path.getmtime(os.path.join(folder, f)) for f in os.listdir(folder) if not f.startswith('.')]
    return max(times) if times else 0


def archive_latest():
    for prog in PROGRAMS:
        src = os.path.join(WWW, prog)
        if not os.path.isfile(os.path.join(src, 'index.htm')):
            continue
        last = newest(src)
        day = datetime.fromtimestamp(last, TZ).strftime('%Y-%m-%d')
        dst = os.path.join(ARCHIVE, prog, day)
        if os.path.isdir(dst) and newest(dst) >= last:
            continue
        os.makedirs(dst, exist_ok=True)
        for f in os.listdir(src):
            p = os.path.join(src, f)
            # Pages for classes that did not run are left over from an earlier event: skip them.
            if not f.startswith('.') and os.path.isfile(p) and os.path.getmtime(p) >= last - FRESH:
                shutil.copy2(p, dst)
        stamp = os.path.join(dst, STAMP)
        if os.path.exists(stamp):
            os.remove(stamp)        # changed since it went to Drive: send it again
        print(f'archived {prog} {day}')


def write_index():
    events = []
    for prog, name in PROGRAMS.items():
        base = os.path.join(ARCHIVE, prog)
        for day in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            if os.path.isfile(os.path.join(base, day, 'index.htm')):
                events.append({'program': name, 'date': day, 'url': f'archive/{prog}/{day}/'})
    events.sort(key=lambda e: (e['date'], e['program']), reverse=True)
    os.makedirs(ARCHIVE, exist_ok=True)
    tmp = os.path.join(ARCHIVE, 'index.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(events, f, indent=1)
    os.replace(tmp, os.path.join(ARCHIVE, 'index.json'))
    for root, dirs, files in os.walk(ARCHIVE):
        os.chmod(root, 0o755)
        for f in files:
            os.chmod(os.path.join(root, f), 0o644)
    return events


def drive():
    cr = service_account.Credentials.from_service_account_file(
        SA, scopes=['https://www.googleapis.com/auth/drive']).with_subject('admin@drscca.org')
    return AuthorizedSession(cr)


def folder(s, name, parent):
    q = (f"name = '{name}' and '{parent}' in parents and trashed = false "
         "and mimeType = 'application/vnd.google-apps.folder'")
    r = s.get(API, params=dict(q=q, corpora='drive', driveId=REGION_DRIVE, includeItemsFromAllDrives='true',
                               supportsAllDrives='true', fields='files(id)')).json()
    if r.get('files'):
        return r['files'][0]['id']
    r = s.post(API, params=dict(supportsAllDrives='true', fields='id'),
               json=dict(name=name, parents=[parent], mimeType='application/vnd.google-apps.folder'))
    r.raise_for_status()
    return r.json()['id']


def to_drive():
    pending = [(prog, day) for prog in PROGRAMS if os.path.isdir(os.path.join(ARCHIVE, prog))
               for day in sorted(os.listdir(os.path.join(ARCHIVE, prog)))
               if not os.path.exists(os.path.join(ARCHIVE, prog, day, STAMP))]
    if not pending:
        return
    s = drive()
    top = REGION_DRIVE
    for name in DRIVE_PATH:
        top = folder(s, name, top)
    for prog, day in pending:
        local = os.path.join(ARCHIVE, prog, day)
        dest = folder(s, day, folder(s, PROGRAMS[prog], top))
        have = {f['name']: f['id'] for f in s.get(API, params=dict(
            q=f"'{dest}' in parents and trashed = false", corpora='drive', driveId=REGION_DRIVE,
            includeItemsFromAllDrives='true', supportsAllDrives='true', pageSize=1000, fields='files(id,name)')).json().get('files', [])}
        for f in sorted(os.listdir(local)):
            if f.startswith('.'):
                continue
            with open(os.path.join(local, f), 'rb') as fh:
                body = fh.read()
            if f in have:
                r = s.patch(f'{UPLOAD}/{have[f]}', params=dict(uploadType='media', supportsAllDrives='true'),
                            data=body, headers={'Content-Type': 'text/html'})
            else:
                meta = json.dumps(dict(name=f, parents=[dest]))
                r = s.post(UPLOAD, params=dict(uploadType='multipart', supportsAllDrives='true'), files={
                    'metadata': ('metadata', meta, 'application/json; charset=UTF-8'),
                    'file': (f, body, 'text/html')})
            r.raise_for_status()
        open(os.path.join(local, STAMP), 'w').close()
        print(f'copied to Drive: {PROGRAMS[prog]} {day}')


if __name__ == '__main__':
    archive_latest()
    events = write_index()
    print(f'{len(events)} archived events')
    if '--no-drive' not in sys.argv:
        to_drive()
