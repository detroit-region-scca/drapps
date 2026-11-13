#!/usr/bin/env python3
"""Nightly off-site copy of the region's files, outside Google Drive (drscca-offsite-backup.timer).

Every shared drive the admin account can see (found each night, so a new shared drive is included without a change
here) is mounted read-only with rclone and backed up with restic, together with its Google Docs, Sheets and Slides
exported as Word, Excel and PowerPoint files, and the app's own files (app/data). restic keeps deduplicated,
encrypted snapshots in a Cloud Storage bucket in the region's Google Cloud project, so a night only uploads what
changed. The bucket's soft delete keeps anything deleted, by anyone, restorable for 90 days.

Settings, in data/secrets/offsite-backup.env (secrets folder, so the weekly secrets copy carries them):
    RESTIC_REPOSITORY=gs:<bucket>:/restic
    RESTIC_PASSWORD=...          without it the backups cannot be read: it is also in the Account Recovery copy
    GOOGLE_APPLICATION_CREDENTIALS=/opt/drscca/data/secrets/workspace-sa.json
Restore and checks: docs/RUNBOOK.md, "Off-site copy".
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date

SA = os.environ.get('WORKSPACE_SA') or '/opt/drscca/data/secrets/workspace-sa.json'
ADMIN = 'admin@drscca.org'
WORK = os.environ.get('OFFSITE_WORK') or '/var/lib/drscca-offsite'
APP_DATA = os.environ.get('APP_DATA_DIR') or '/opt/drscca/app/data'
# The nightly sheet copies in app/data/backups are left out: the sheets themselves are exported from Drive.
EXCLUDE = [f'{APP_DATA}/backups', '*.lock', '*.tmp']
KEEP = ['--keep-daily', '14', '--keep-weekly', '8', '--keep-monthly', '24']
EXPORT = 'docx,xlsx,pptx,svg'
RCLONE_ENV = {'RCLONE_CONFIG_GD_TYPE': 'drive', 'RCLONE_CONFIG_GD_SCOPE': 'drive.readonly',
              'RCLONE_CONFIG_GD_SERVICE_ACCOUNT_FILE': SA, 'RCLONE_CONFIG_GD_IMPERSONATE': ADMIN}


def run(cmd, **kw):
    print('+', ' '.join(cmd), flush=True)
    return subprocess.run(cmd, check=True, env=dict(os.environ, **RCLONE_ENV), **kw)


def shared_drives():
    """[{'id', 'name'}] of every shared drive, as the admin account sees them."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(
        SA, scopes=['https://www.googleapis.com/auth/drive.readonly'], subject=ADMIN)
    s, out, tok = AuthorizedSession(creds), [], None
    while True:
        r = s.get('https://www.googleapis.com/drive/v3/drives', params=dict(pageSize=100, **({'pageToken': tok} if tok else {})))
        r.raise_for_status()
        out += r.json().get('drives', [])
        tok = r.json().get('nextPageToken')
        if not tok:
            return out


def folder_name(name):
    return re.sub(r'[^\w .()-]', '_', name).strip() or 'drive'


def mount(drive_id, path):
    os.makedirs(path, exist_ok=True)
    if os.path.ismount(path):
        subprocess.run(['fusermount3', '-u', path], check=False)
    run(['rclone', 'mount', 'gd:', path, '--drive-team-drive', drive_id, '--read-only', '--drive-skip-gdocs',
         '--vfs-cache-mode', 'off', '--dir-cache-time', '6h', '--attr-timeout', '6h', '--poll-interval', '0', '--daemon'])
    for _ in range(60):
        if os.path.ismount(path):
            return
        time.sleep(1)
    raise SystemExit(f'The shared drive did not mount at {path}.')


def export_docs(drive_id, path):
    """The drive's Google Docs, Sheets and Slides (the files rclone lists with no size) as Office files."""
    listing = json.loads(run(['rclone', 'lsjson', '-R', '--files-only', '--drive-team-drive', drive_id,
                              '--drive-export-formats', EXPORT, 'gd:'], capture_output=True, text=True).stdout or '[]')
    docs = sorted(f['Path'] for f in listing if f.get('Size', 0) < 0)
    os.makedirs(path, exist_ok=True)
    lst = os.path.join(WORK, 'docs-list.txt')
    with open(lst, 'w') as f:
        f.write(''.join(p + '\n' for p in docs))
    run(['rclone', 'sync', 'gd:', path, '--drive-team-drive', drive_id, '--drive-export-formats', EXPORT,
         '--files-from-raw', lst, '--ignore-size'])
    return len(docs)


def restic(*args):
    return run(['restic', '--host', 'drapps', *args])


XATTR = re.compile(r'^error: NodeFromFileInfo: xattr\.list .*: input/output error$')


def backup(args):
    """restic backup. A Drive mount cannot list extended attributes, so restic reports each file with an xattr error
    and ends with exit 3 ("some files could not be read") although every file's content was saved. Those lines alone
    are not a failure; any other error is."""
    cmd = ['restic', '--host', 'drapps', 'backup', *args]
    print('+', ' '.join(cmd), flush=True)
    p = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, env=dict(os.environ, **RCLONE_ENV))
    xattr, other = 0, 0
    for line in p.stderr:
        line = line.rstrip('\n')
        if XATTR.match(line):
            xattr += 1
            continue
        if line.startswith('error:') or 'Fatal' in line:
            other += 1
        print(line, file=sys.stderr, flush=True)
    rc = p.wait()
    if xattr:
        print(f'{xattr} files without extended attributes (normal for a Drive mount).', flush=True)
    if rc == 0 or (rc == 3 and not other):
        return
    raise SystemExit(f'restic backup ended with exit {rc} and {other} errors (see above).')


def main():
    for k in ('RESTIC_REPOSITORY', 'RESTIC_PASSWORD', 'GOOGLE_APPLICATION_CREDENTIALS'):
        if not os.environ.get(k):
            raise SystemExit(f'{k} is not set (data/secrets/offsite-backup.env).')
    drives_dir, docs_dir = os.path.join(WORK, 'drives'), os.path.join(WORK, 'docs')
    os.makedirs(drives_dir, exist_ok=True)
    found = shared_drives()
    if not found:
        raise SystemExit('No shared drives were found; nothing was backed up.')
    names = {d['id']: folder_name(d['name']) for d in found}
    for gone in set(os.listdir(docs_dir) if os.path.isdir(docs_dir) else []) - set(names.values()):
        shutil.rmtree(os.path.join(docs_dir, gone))          # a shared drive that no longer exists
    mounted = []
    try:
        for d in found:
            mnt = os.path.join(drives_dir, names[d['id']])
            mount(d['id'], mnt)
            mounted.append(mnt)
            print(f"{d['name']}: {export_docs(d['id'], os.path.join(docs_dir, names[d['id']]))} Google files exported", flush=True)
        # Unchanged files are skipped by size and time; a mounted drive gives no stable inode or change time.
        backup(['--tag', 'nightly', '--ignore-inode', '--ignore-ctime', *sum((['--exclude', e] for e in EXCLUDE), []),
                *mounted, docs_dir, APP_DATA])
    finally:
        for mnt in mounted:
            subprocess.run(['fusermount3', '-u', mnt], check=False)
    if date.today().weekday() == 6:          # Sundays: drop old snapshots and check the repository
        restic('forget', '--tag', 'nightly', '--prune', *KEEP)
        restic('check')


if __name__ == '__main__':
    sys.exit(main())
