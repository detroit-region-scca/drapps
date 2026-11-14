#!/usr/bin/env python3
"""Nightly off-site copy of everything the region keeps, outside Google Drive (drscca-offsite-backup.timer).

Into the Cloud Storage bucket (OFFSITE_BUCKET, in the region's Google Cloud project), under current/:
    drives/<shared drive>/        every file in every shared drive (each found nightly, so new ones are included)
    google-files/<shared drive>/  its Google Docs, Sheets and Slides as Word, Excel and PowerPoint files
    server/app-data/              the app's own files (app/data, without its nightly sheet copies)
    quickbooks/                   the books, every type as JSON (written by tools/qbo_books.mjs in the nightly sync)
    website-media/                every image and file uploaded to drscca.org (added, never removed)
    mail/admin/                   every message in the admin@drscca.org mailbox as .eml (added, never removed)

Only new or changed files are sent: rclone compares Drive's and the bucket's checksums, so an unchanged file is never
downloaded again. A file that changed or was deleted is not overwritten: its old copy moves to history/<date>/ with
the same path and stays a year (the bucket's lifecycle rule). On top of that the bucket's soft delete keeps anything
deleted, by anyone, restorable for 90 days, and nothing on this server can change those bucket settings.

Each part runs on its own: one that fails does not stop the others, and the job ends with an error (so admin@ is
emailed) naming each part that failed. A night that would delete more than MAX_DELETE files from one drive's copy
stops that drive instead (a wiped or encrypted drive should not be mirrored). After copying, every shared drive is
checked file by file (by checksum) against its copy. Once a month, after a complete night, everything in current/
is also copied to the vault bucket (OFFSITE_VAULT) under <YYYY-MM>/, where a locked retention policy keeps it a year.
"""
import base64
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime

SA = os.environ.get('WORKSPACE_SA') or '/opt/drscca/data/secrets/workspace-sa.json'
ADMIN = 'admin@drscca.org'
BUCKET = os.environ.get('OFFSITE_BUCKET') or 'drscca-org-backups'
# Monthly locked copies: a bucket with a locked 1-year retention policy, so nobody (admin@ included) can delete or
# change a month's copy for a year; its lifecycle rule removes each one after that (12 months kept).
VAULT = os.environ.get('OFFSITE_VAULT') or 'drscca-org-vault'
APP_DATA = os.environ.get('APP_DATA_DIR') or '/opt/drscca/app/data'
BOOKS = os.environ.get('QBO_BOOKS_DIR') or '/var/lib/drscca-quickbooks'
SITE = 'https://drscca.org'
UPLOADS = f'{SITE}/wp-content/uploads/'
WORK = os.environ.get('OFFSITE_WORK') or '/var/lib/drscca-offsite'
MAX_DELETE = int(os.environ.get('OFFSITE_MAX_DELETE') or 300)
EXPORT = 'docx,xlsx,pptx,svg'
TODAY = date.today().isoformat()
RCLONE_ENV = {
    'RCLONE_CONFIG_GD_TYPE': 'drive', 'RCLONE_CONFIG_GD_SCOPE': 'drive.readonly',
    'RCLONE_CONFIG_GD_SERVICE_ACCOUNT_FILE': SA, 'RCLONE_CONFIG_GD_IMPERSONATE': ADMIN,
    'RCLONE_CONFIG_GCS_TYPE': 'google cloud storage', 'RCLONE_CONFIG_GCS_SERVICE_ACCOUNT_FILE': SA,
    'RCLONE_CONFIG_GCS_BUCKET_POLICY_ONLY': 'true',
}
COMMON = ['--fast-list', '--log-level', 'NOTICE', '--stats', '0', '--retries', '5', '--low-level-retries', '20']


def run(cmd, capture=False):
    print('+', ' '.join(cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, capture_output=capture, env=dict(os.environ, **RCLONE_ENV))


def dest(path):
    return f'gcs:{BUCKET}/current/{path}'


def history(path):
    return f'gcs:{BUCKET}/history/{TODAY}/{path}'


def sync(src, path, *extra):
    """src to current/<path>; what it replaces or removes moves to history/<today>/<path>."""
    run(['rclone', 'sync', src, dest(path), '--backup-dir', history(path), *COMMON, *extra])


def google(scope):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    sub = {} if 'devstorage' in scope else {'subject': ADMIN}
    return AuthorizedSession(service_account.Credentials.from_service_account_file(SA, scopes=[scope], **sub))


def shared_drives():
    s, out, tok = google('https://www.googleapis.com/auth/drive.readonly'), [], None
    while True:
        r = s.get('https://www.googleapis.com/drive/v3/drives', params=dict(pageSize=100, **({'pageToken': tok} if tok else {})))
        r.raise_for_status()
        out += r.json().get('drives', [])
        tok = r.json().get('nextPageToken')
        if not tok:
            return out


def folder_name(name):
    import re
    return re.sub(r'[^\w .()-]', '_', name).strip() or 'drive'


def listing(path):
    """Names under current/<path>, relative to it."""
    out = run(['rclone', 'lsf', '-R', '--files-only', '--fast-list', dest(path)], capture=True).stdout
    return {line for line in out.splitlines() if line}


def one_drive(d):
    name = folder_name(d['name'])
    # Shortcuts whose target is gone or not shared cannot be read; they hold no data, so they are skipped.
    team = ['--drive-team-drive', d['id'], '--drive-skip-dangling-shortcuts']
    # Uploaded files, compared by checksum; a night that would remove many of them stops here instead.
    sync('gd:', f'drives/{name}', *team, '--drive-skip-gdocs', '--checksum', '--max-delete', str(MAX_DELETE))
    # Google files exported (their export has no checksum or size: compared by modified time).
    docs = json.loads(run(['rclone', 'lsjson', '-R', '--files-only', *team, '--drive-export-formats', EXPORT, 'gd:'],
                          capture=True).stdout or '[]')
    lst = os.path.join(WORK, 'google-files.txt')
    with open(lst, 'w') as f:
        f.write(''.join(x['Path'] + '\n' for x in docs if x.get('Size', 0) < 0))
    sync('gd:', f'google-files/{name}', *team, '--drive-export-formats', EXPORT, '--files-from-raw', lst,
         '--ignore-size', '--delete-excluded')
    # Proof, not trust: every uploaded file in the drive must be in the copy with the same checksum.
    run(['rclone', 'check', 'gd:', dest(f'drives/{name}'), *team, '--drive-skip-gdocs', '--one-way', '--checksum',
         '--fast-list', '--log-level', 'NOTICE'])
    return f"{d['name']}: copied and checked"


def app_data():
    sync(APP_DATA, 'server/app-data', '--checksum', '--exclude', 'backups/**', '--exclude', '*.lock', '--exclude', '*.tmp')
    return 'app files copied'


def quickbooks():
    if not os.path.isdir(BOOKS) or not os.listdir(BOOKS):
        raise RuntimeError(f'No QuickBooks copy in {BOOKS}: the nightly sync writes it (tools/qbo_books.mjs).')
    newest = max(os.path.getmtime(os.path.join(BOOKS, f)) for f in os.listdir(BOOKS))
    if (datetime.now().timestamp() - newest) > 3 * 86400:
        raise RuntimeError(f'The QuickBooks copy in {BOOKS} is more than 3 days old: check drscca-qbo.')
    sync(BOOKS, 'quickbooks', '--checksum')
    return 'QuickBooks books copied'


def website_media():
    """Every file in the site's media library, by its address under /wp-content/uploads/. Only new ones are fetched."""
    want, page = set(), 1
    while True:
        req = urllib.request.Request(f'{SITE}/wp-json/wp/v2/media?per_page=100&page={page}&_fields=source_url',
                                     headers={'User-Agent': 'DRSCCA backup'})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                pages = int(r.headers.get('X-WP-TotalPages') or 1)
                items = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 400 and page > 1:      # past the last page
                break
            raise
        want |= {m['source_url'].split('/wp-content/uploads/', 1)[1] for m in items
                 if '/wp-content/uploads/' in (m.get('source_url') or '')}
        if page >= pages:
            break
        page += 1
    if len(want) < 100:
        raise RuntimeError(f'Only {len(want)} media files listed by drscca.org; not trusting that.')
    new = sorted(want - listing('website-media'))
    gs = google('https://www.googleapis.com/auth/devstorage.read_write')
    failed = []
    for path in new:
        try:
            req = urllib.request.Request(UPLOADS + urllib.parse.quote(path), headers={'User-Agent': 'DRSCCA backup'})
            with urllib.request.urlopen(req, timeout=120) as r:
                data, kind = r.read(), r.headers.get('Content-Type') or 'application/octet-stream'
            gs.post(f'https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o',
                    params={'uploadType': 'media', 'name': f'current/website-media/{path}'},
                    data=data, headers={'Content-Type': kind}).raise_for_status()
        except Exception as e:          # one missing file must not stop the rest; it is tried again tomorrow
            failed.append(f'{path}: {e}')
    if failed:
        raise RuntimeError(f'{len(failed)} of {len(new)} new media files could not be copied, first: {failed[0]}')
    return f'website media: {len(new)} new of {len(want)}'


def mail():
    """Every message in admin@'s mailbox as an .eml file; a message deleted from Gmail stays in the copy."""
    # The scope admin@'s delegation allows (it does not list gmail.readonly). This only ever reads.
    gm = google('https://mail.google.com/')
    gs = google('https://www.googleapis.com/auth/devstorage.read_write')
    have = {p.rsplit('/', 1)[-1][:-4] for p in listing('mail/admin') if p.endswith('.eml')}
    ids, tok = [], None
    while True:
        r = gm.get('https://gmail.googleapis.com/gmail/v1/users/me/messages',
                   params=dict(maxResults=500, includeSpamTrash='false', **({'pageToken': tok} if tok else {})))
        r.raise_for_status()
        ids += [m['id'] for m in r.json().get('messages', [])]
        tok = r.json().get('nextPageToken')
        if not tok:
            break
    new = [i for i in ids if i not in have]
    for i in new:
        r = gm.get(f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{i}', params={'format': 'raw'})
        r.raise_for_status()
        m = r.json()
        when = datetime.fromtimestamp(int(m.get('internalDate', 0)) / 1000)
        name = f"current/mail/admin/{when:%Y/%m}/{i}.eml"
        u = gs.post(f'https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o',
                    params={'uploadType': 'media', 'name': name, 'ifGenerationMatch': '0'},
                    data=base64.urlsafe_b64decode(m['raw']), headers={'Content-Type': 'message/rfc822'})
        if u.status_code not in (200, 412):          # 412: already there
            u.raise_for_status()
    return f'mail: {len(new)} new of {len(ids)} messages'


def vault():
    """Once a month, after a complete night: copy everything in current/ to the vault as <YYYY-MM>/. The copy runs
    inside Cloud Storage (nothing passes through this server) and ends with <YYYY-MM>/status.json, which marks the
    month done; a month whose copy did not finish is tried again the next night."""
    month = date.today().strftime('%Y-%m')
    gs = google('https://www.googleapis.com/auth/devstorage.read_only')
    done = gs.get(f'https://storage.googleapis.com/storage/v1/b/{VAULT}/o/{month}%2Fstatus.json')
    if done.status_code == 200:
        return f'vault: {month} already copied'
    run(['rclone', 'copy', f'gcs:{BUCKET}/current', f'gcs:{VAULT}/{month}', '--gcs-storage-class', 'ARCHIVE',
         '--fast-list', '--log-level', 'NOTICE', '--stats', '0', '--retries', '5', '--transfers', '16', '--checkers', '16'])
    w = google('https://www.googleapis.com/auth/devstorage.read_write')
    w.post(f'https://storage.googleapis.com/upload/storage/v1/b/{VAULT}/o',
           params={'uploadType': 'media', 'name': f'{month}/status.json', 'ifGenerationMatch': '0'},
           data=json.dumps({'copied': datetime.now().astimezone().isoformat(timespec='seconds'), 'from': f'gs://{BUCKET}/current'}),
           headers={'Content-Type': 'application/json'}).raise_for_status()
    return f'vault: {month} copied and locked for a year'


def status(results, failed):
    """current/status.json: when the last run ended and what it did (read by people and the staleness alert)."""
    gs = google('https://www.googleapis.com/auth/devstorage.read_write')
    doc = {'finished': datetime.now().astimezone().isoformat(timespec='seconds'), 'ok': not failed,
           'done': results, 'failed': failed}
    gs.post(f'https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o',
            params={'uploadType': 'media', 'name': 'current/status.json'},
            data=json.dumps(doc, indent=1), headers={'Content-Type': 'application/json'}).raise_for_status()


def check(hours=36):
    """--check (drscca-offsite-check.timer): fail, so admin@ is emailed, unless a complete copy finished in the last
    36 hours. Catches a copy that stopped running, which its own failure email cannot."""
    r = google('https://www.googleapis.com/auth/devstorage.read_only').get(
        f'https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/current%2Fstatus.json', params={'alt': 'media'})
    if r.status_code == 404:
        raise SystemExit('The off-site copy has never finished (no current/status.json in the bucket).')
    r.raise_for_status()
    doc = r.json()
    age = (datetime.now().astimezone() - datetime.fromisoformat(doc['finished'])).total_seconds() / 3600
    if age > hours:
        raise SystemExit(f"The off-site copy last finished {age:.0f} hours ago ({doc['finished']}): check drscca-offsite-backup.")
    if not doc.get('ok'):
        raise SystemExit('The last off-site copy did not complete: ' + '; '.join(doc.get('failed') or []))
    month = date.today().strftime('%Y-%m')
    v = google('https://www.googleapis.com/auth/devstorage.read_only').get(
        f'https://storage.googleapis.com/storage/v1/b/{VAULT}/o/{month}%2Fstatus.json')
    if v.status_code != 200 and date.today().day >= 3:
        raise SystemExit(f'No locked vault copy for {month} yet (gs://{VAULT}/{month}/): check drscca-offsite-backup.')
    print(f"Off-site copy OK, finished {doc['finished']}: " + '; '.join(doc.get('done') or []))


def main():
    if '--check' in sys.argv:
        return check()
    os.makedirs(WORK, exist_ok=True)
    parts = [(f"shared drive {d['name']}", (lambda d=d: one_drive(d))) for d in shared_drives()]
    if not parts:
        raise SystemExit('No shared drives were found; nothing was copied.')
    parts += [('app files', app_data), ('QuickBooks', quickbooks), ('website media', website_media), ('admin@ mail', mail)]
    results, failed = [], []
    for label, fn in parts:
        try:
            results.append(fn())
            print('OK', results[-1], flush=True)
        except Exception as e:
            failed.append(f'{label}: {e}')
            print('FAILED', failed[-1], file=sys.stderr, flush=True)
    if not failed:          # only a complete night goes into the vault
        try:
            results.append(vault())
            print('OK', results[-1], flush=True)
        except Exception as e:
            failed.append(f'vault copy: {e}')
            print('FAILED', failed[-1], file=sys.stderr, flush=True)
    status(results, failed)
    if failed:
        raise SystemExit('Parts of the off-site copy failed:\n' + '\n'.join(failed))


if __name__ == '__main__':
    main()
