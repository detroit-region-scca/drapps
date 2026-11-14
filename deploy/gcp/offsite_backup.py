#!/usr/bin/env python3
"""Nightly off-site copy of everything the region keeps, outside Google Drive (drscca-offsite-backup.timer), with
restic: deduplicated, compressed, encrypted snapshots in a Cloud Storage bucket in the region's Google Cloud project.

What goes into each snapshot:
    drives/<shared drive>/        every file in every shared drive, read through a read-only rclone mount (each
                                  shared drive is found nightly, so a new one is included without a change here)
    duplicates/<shared drive>/    every file that shares its name with another in the same folder (the mount shows
                                  only one of them), downloaded by its Drive id
    google-files/<shared drive>/  its Google Docs, Sheets and Slides as Word, Excel and PowerPoint files
    website-media/                every image and file uploaded to drscca.org (added, never removed)
    mail/admin/                   the admin@drscca.org mailbox: every message as .eml plus its labels, by Got Your Back
                                  (added, never removed; restorable into Gmail with gyb --action restore)
    /var/lib/drscca-quickbooks    the books, every QuickBooks record type as JSON (tools/qbo_books.mjs, nightly sync)
    /opt/drscca/app/data          the app's own files (not its nightly sheet copies in backups/)
    /opt/drscca/data/secrets ...  the server's secrets and the live timing logins and host keys (SECRETS below)

Snapshots kept: 14 daily, 8 weekly, 24 monthly (Sundays: forget, prune, check). Proof, not trust: after each backup
the number of files restic saved for each shared drive must equal the number of distinct names Drive lists (the rest
are in duplicates/), or the night fails.

Ransom protection: the bucket keeps anything deleted restorable for 90 days (soft delete), and every night the new
restic files are also copied into the vault bucket (OFFSITE_VAULT), whose locked retention policy keeps each file a
year and lets nobody (admin@ included) delete or change it. restic never changes a file once written, so the vault
is an append-only, deduplicated copy of the repository; restore from it with restic --no-lock.

Each part runs on its own; the job ends with an error (so admin@ is emailed) naming every part that failed.
Settings in data/secrets/offsite-backup.env (RESTIC_REPOSITORY, RESTIC_PASSWORD, GOOGLE_APPLICATION_CREDENTIALS,
GOOGLE_PROJECT_ID). Without RESTIC_PASSWORD the backups cannot be read: it is also on the printed recovery sheet
(and its PDF in Account Recovery), never only on this server. Restore and checks: docs/RUNBOOK.md, "Off-site copy".
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime

SA = os.environ.get('WORKSPACE_SA') or '/opt/drscca/data/secrets/workspace-sa.json'
ADMIN = 'admin@drscca.org'
BUCKET = os.environ.get('OFFSITE_BUCKET') or 'drscca-org-backups'
VAULT = os.environ.get('OFFSITE_VAULT') or 'drscca-org-vault'
APP_DATA = os.environ.get('APP_DATA_DIR') or '/opt/drscca/app/data'
BOOKS = os.environ.get('QBO_BOOKS_DIR') or '/var/lib/drscca-quickbooks'
# The server's secrets (app settings, service account keys, QuickBooks token, officials list, GYB and GAM config,
# this job's own settings) and the live timing logins and SFTP host keys: what a rebuilt server needs. Only readable
# with the restic password, which is kept off the server (the printed recovery sheet).
SECRETS = ['/opt/drscca/data/secrets', '/opt/drscca/data/livetiming/users.conf', '/opt/drscca/data/livetiming/keys']
WORK = os.environ.get('OFFSITE_WORK') or '/var/lib/drscca-offsite'
GYB = os.environ.get('GYB') or '/opt/drscca/gyb/gyb'
GYB_CONFIG = os.environ.get('GYB_CONFIG') or '/opt/drscca/data/secrets/gyb'
SITE = 'https://drscca.org'
UPLOADS = f'{SITE}/wp-content/uploads/'
EXPORT = 'docx,xlsx,pptx,svg'
KEEP = ['--keep-daily', '14', '--keep-weekly', '8', '--keep-monthly', '24']
EXCLUDE = [f'{APP_DATA}/backups', '*.lock', '*.tmp', '*.part']
RCLONE_ENV = {
    'RCLONE_CONFIG_GD_TYPE': 'drive', 'RCLONE_CONFIG_GD_SCOPE': 'drive.readonly',
    'RCLONE_CONFIG_GD_SERVICE_ACCOUNT_FILE': SA, 'RCLONE_CONFIG_GD_IMPERSONATE': ADMIN,
    'RCLONE_CONFIG_GCS_TYPE': 'google cloud storage', 'RCLONE_CONFIG_GCS_SERVICE_ACCOUNT_FILE': SA,
    'RCLONE_CONFIG_GCS_BUCKET_POLICY_ONLY': 'true',
}
DRIVE_FLAGS = ['--drive-skip-gdocs', '--drive-skip-dangling-shortcuts']
# restic 0.17.1+ keeps a file whose extended attributes cannot be read (a Drive mount has none to give) and warns
# "incomplete metadata"; that alone, with exit code 3, is a complete backup. Any other warning or error is not.
SUMMARY = 'Warning: at least one source file could not be read'
HARMLESS = re.compile(r'^(error: )?incomplete metadata for .*: xattr\.list .*$')      # restic 0.18 prefixes it with "error: "


def env():
    return dict(os.environ, **RCLONE_ENV, RESTIC_CACHE_DIR=os.path.join(WORK, 'restic-cache'), GOGC='50')


def run(cmd, capture=False):
    print('+', ' '.join(cmd), flush=True)
    return subprocess.run(cmd, check=True, text=True, capture_output=capture, env=env())


def google(scope):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    sub = {} if 'devstorage' in scope else {'subject': ADMIN}
    return AuthorizedSession(service_account.Credentials.from_service_account_file(SA, scopes=[scope], **sub))


def folder_name(name):
    return re.sub(r'[^\w .()-]', '_', name).strip() or 'drive'


def shared_drives():
    s, out, tok = google('https://www.googleapis.com/auth/drive.readonly'), [], None
    while True:
        r = s.get('https://www.googleapis.com/drive/v3/drives', params=dict(pageSize=100, **({'pageToken': tok} if tok else {})))
        r.raise_for_status()
        out += r.json().get('drives', [])
        tok = r.json().get('nextPageToken')
        if not tok:
            return out


# ---------- gathering: what restic reads ----------

def mount(drive_id, path):
    os.makedirs(path, exist_ok=True)
    if os.path.ismount(path):
        subprocess.run(['fusermount3', '-u', path], check=False)
    run(['rclone', 'mount', 'gd:', path, '--drive-team-drive', drive_id, '--read-only', *DRIVE_FLAGS,
         '--vfs-cache-mode', 'off', '--dir-cache-time', '6h', '--attr-timeout', '6h', '--poll-interval', '0', '--daemon'])
    for _ in range(60):
        if os.path.ismount(path):
            return
        time.sleep(1)
    raise RuntimeError(f'The shared drive did not mount at {path}.')


def drive_listing(drive_id):
    """[{'Path', 'ID', ...}] of every uploaded file in this shared drive."""
    return json.loads(run(['rclone', 'lsjson', '-R', '--files-only', '--fast-list', '--drive-team-drive', drive_id,
                           *DRIVE_FLAGS, 'gd:'], capture=True).stdout or '[]')


def dup_groups(listing):
    """(files that share their folder and name with another file, number of distinct names)."""
    from collections import defaultdict
    groups = defaultdict(list)
    for f in listing:
        groups[f['Path']].append(f)
    return [f for fs in groups.values() if len(fs) > 1 for f in fs], len(groups)


def duplicates(listing, path):
    """Drive lets two files have the same name in the same folder; the mount shows only one of them. Every file in
    such a group is downloaded here by its Drive id ('<folder>/<name> [<id>]'), so none is left out; one changed since
    the last night is downloaded again. Returns (how many such files, distinct names in the drive)."""
    dupes, distinct = dup_groups(listing)
    index_file = path.rstrip('/') + '.json'           # {saved path: Drive modified time}, beside the folder
    try:
        with open(index_file) as f:
            index = json.load(f)
    except (OSError, ValueError):
        index = {}
    s = google('https://www.googleapis.com/auth/drive.readonly')
    want = {}
    for f in dupes:
        folder, name = os.path.split(f['Path'])
        dest = os.path.join(path, folder, f"{name} [{f['ID']}]")
        want[dest] = f.get('ModTime', '')
        if os.path.exists(dest) and index.get(dest) == want[dest]:
            continue
        r = s.get(f"https://www.googleapis.com/drive/v3/files/{f['ID']}", params={'alt': 'media', 'supportsAllDrives': 'true'})
        r.raise_for_status()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest + '.part', 'wb') as out:
            out.write(r.content)
        os.replace(dest + '.part', dest)
    for root, _, files in os.walk(path):              # a copy whose twin is gone: the mount has it now
        for n in files:
            if os.path.join(root, n) not in want:
                os.remove(os.path.join(root, n))
    with open(index_file, 'w') as f:
        json.dump(want, f)
    return len(dupes), distinct


def export_docs(drive_id, path):
    """The drive's Google Docs, Sheets and Slides (the files rclone lists with no size) as Office files."""
    listing = json.loads(run(['rclone', 'lsjson', '-R', '--files-only', '--drive-team-drive', drive_id,
                              '--drive-skip-dangling-shortcuts', '--drive-export-formats', EXPORT, 'gd:'], capture=True).stdout or '[]')
    lst = os.path.join(WORK, 'google-files.txt')
    with open(lst, 'w') as f:
        f.write(''.join(x['Path'] + '\n' for x in listing if x.get('Size', 0) < 0))
    os.makedirs(path, exist_ok=True)
    run(['rclone', 'sync', 'gd:', path, '--drive-team-drive', drive_id, '--drive-skip-dangling-shortcuts',
         '--drive-export-formats', EXPORT, '--files-from-raw', lst, '--ignore-size', '--delete-excluded',
         '--log-level', 'NOTICE', '--stats', '0'])


def website_media():
    """Every file in the site's public media library, by its address under /wp-content/uploads/; only new ones are
    downloaded, and nothing is ever removed (a file deleted from the site stays in the backup)."""
    base, want, page = os.path.join(WORK, 'website-media'), set(), 1
    while True:
        req = urllib.request.Request(f'{SITE}/wp-json/wp/v2/media?per_page=100&page={page}&_fields=source_url',
                                     headers={'User-Agent': 'DRSCCA backup'})
        with urllib.request.urlopen(req, timeout=60) as r:
            pages = int(r.headers.get('X-WP-TotalPages') or 1)
            want |= {m['source_url'].split('/wp-content/uploads/', 1)[1] for m in json.load(r)
                     if '/wp-content/uploads/' in (m.get('source_url') or '')}
        if page >= pages:
            break
        page += 1
    if len(want) < 100:
        raise RuntimeError(f'Only {len(want)} media files listed by drscca.org; not trusting that.')
    new = [p for p in sorted(want) if not os.path.exists(os.path.join(base, p))]
    failed = []
    for p in new:
        try:
            req = urllib.request.Request(UPLOADS + urllib.parse.quote(p), headers={'User-Agent': 'DRSCCA backup'})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            dest = os.path.join(base, p)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest + '.part', 'wb') as f:
                f.write(data)
            os.replace(dest + '.part', dest)
        except Exception as e:          # one missing file must not stop the rest; it is tried again tomorrow
            failed.append(f'{p}: {e}')
    if failed:
        raise RuntimeError(f'{len(failed)} of {len(new)} new media files could not be fetched, first: {failed[0]}')
    return f'website media: {len(new)} new of {len(want)}'


def mail():
    """admin@'s whole mailbox with Got Your Back (GYB, /opt/drscca/gyb): every message as .eml, with its Gmail labels
    in GYB's database (msg-db.sqlite), so mail can be put back into Gmail with gyb --action restore. Only new messages
    are fetched; a message deleted from Gmail stays. GYB signs in as admin@ through the automation account's
    domain-wide delegation (its config folder data/secrets/gyb holds a link to workspace-sa.json)."""
    base = os.path.join(WORK, 'mail', 'admin')
    os.makedirs(base, exist_ok=True)
    run([GYB, '--config-folder', GYB_CONFIG, '--email', ADMIN, '--service-account', '--action', 'backup',
         '--local-folder', base], capture=True)
    n = sum(1 for _, _, files in os.walk(base) for f in files if f.endswith('.eml'))
    if not os.path.exists(os.path.join(base, 'msg-db.sqlite')) or n < 100:
        raise RuntimeError(f'Got Your Back left {n} messages and no label database in {base}; not trusting that.')
    return f'mail: {n} messages (Got Your Back)'


def books_fresh():
    if not os.path.isdir(BOOKS) or not os.listdir(BOOKS):
        raise RuntimeError(f'No QuickBooks copy in {BOOKS}: the nightly sync writes it (tools/qbo_books.mjs).')
    newest = max(os.path.getmtime(os.path.join(BOOKS, f)) for f in os.listdir(BOOKS))
    if (time.time() - newest) > 3 * 86400:
        raise RuntimeError(f'The QuickBooks copy in {BOOKS} is more than 3 days old: check drscca-qbo.')
    return 'QuickBooks books current'


# ---------- restic ----------

def restic(*args, capture=False):
    return run(['restic', '--host', 'drapps', *args], capture=capture)


def backup(paths):
    """restic backup; exit 3 with only "incomplete metadata" warnings (the Drive mount) is complete."""
    cmd = ['restic', '--host', 'drapps', 'backup', '--tag', 'nightly', '--ignore-inode', '--ignore-ctime',
           *sum((['--exclude', e] for e in EXCLUDE), []), *paths]
    print('+', ' '.join(cmd), flush=True)
    p = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, env=env())
    harmless, other = 0, []
    for line in p.stderr:
        line = line.rstrip('\n')
        if HARMLESS.match(line):
            harmless += 1
            continue
        if line == SUMMARY and harmless:      # restic's closing line for any warning; each real one fails above
            continue
        if line.lower().startswith(('error', 'fatal', 'warning')):
            other.append(line)
        print(line, file=sys.stderr, flush=True)
    rc = p.wait()
    if rc == 0 or (rc == 3 and not other):
        return f'snapshot saved ({harmless} files without extended attributes, normal for a Drive mount)'
    raise RuntimeError(f'restic backup ended with exit {rc}: ' + '; '.join(other[:3]))


def snapshot_counts(mounts):
    """{mount path: files in the latest snapshot under it}."""
    out = restic('ls', '--json', 'latest', capture=True).stdout
    counts = {m: 0 for m in mounts}
    for line in out.splitlines():
        try:
            n = json.loads(line)
        except ValueError:
            continue
        if n.get('type') == 'file':
            for m in mounts:
                if n.get('path', '').startswith(m.rstrip('/') + '/'):
                    counts[m] += 1
    return counts


def vault():
    """Copy the restic files not yet in the vault (inside Cloud Storage). restic never changes a file once written and
    the vault's lock forbids overwriting, so only missing files are copied (--ignore-existing; a copy carries its own
    timestamp, which --immutable would mistake for a change). Lock files are left out."""
    run(['rclone', 'copy', f'gcs:{BUCKET}/restic', f'gcs:{VAULT}/restic', '--ignore-existing', '--exclude', 'locks/**',
         '--gcs-storage-class', 'ARCHIVE', '--fast-list', '--log-level', 'NOTICE', '--stats', '0', '--retries', '5'])
    return 'vault: new restic files copied (kept a year, locked)'


def status(results, failed):
    """status.json beside the repository: when the last run ended and what it did (people, and the noon check)."""
    gs = google('https://www.googleapis.com/auth/devstorage.read_write')
    doc = {'finished': datetime.now().astimezone().isoformat(timespec='seconds'), 'ok': not failed,
           'done': results, 'failed': failed}
    gs.post(f'https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o',
            params={'uploadType': 'media', 'name': 'status.json'},
            data=json.dumps(doc, indent=1), headers={'Content-Type': 'application/json'}).raise_for_status()


def check(hours=36):
    """--check (drscca-offsite-check.timer): fail, so admin@ is emailed, unless a complete night finished in the last
    36 hours. Catches a backup that stopped running, which its own failure email cannot."""
    r = google('https://www.googleapis.com/auth/devstorage.read_only').get(
        f'https://storage.googleapis.com/storage/v1/b/{BUCKET}/o/status.json', params={'alt': 'media'})
    if r.status_code == 404:
        raise SystemExit('The off-site copy has never finished (no status.json in the bucket).')
    r.raise_for_status()
    doc = r.json()
    age = (datetime.now().astimezone() - datetime.fromisoformat(doc['finished'])).total_seconds() / 3600
    if age > hours:
        raise SystemExit(f"The off-site copy last finished {age:.0f} hours ago ({doc['finished']}): check drscca-offsite-backup.")
    if not doc.get('ok'):
        raise SystemExit('The last off-site copy did not complete: ' + '; '.join(doc.get('failed') or []))
    print(f"Off-site copy OK, finished {doc['finished']}: " + '; '.join(doc.get('done') or []))


def main():
    if '--check' in sys.argv:
        return check()
    for k in ('RESTIC_REPOSITORY', 'RESTIC_PASSWORD', 'GOOGLE_APPLICATION_CREDENTIALS'):
        if not os.environ.get(k):
            raise SystemExit(f'{k} is not set (data/secrets/offsite-backup.env).')
    os.makedirs(WORK, exist_ok=True)
    results, failed = [], []

    def part(label, fn):
        try:
            results.append(fn())
            print('OK', results[-1], flush=True)
            return True
        except Exception as e:
            failed.append(f'{label}: {e}')
            print('FAILED', failed[-1], file=sys.stderr, flush=True)
            return False

    def one_drive(d, mnt, name):
        mount(d['id'], mnt)
        listing = drive_listing(d['id'])
        n_dupes, expected[mnt] = duplicates(listing, os.path.join(WORK, 'duplicates', name))
        export_docs(d['id'], os.path.join(WORK, 'google-files', name))
        return (f"{d['name']}: {len(listing)} files in Drive" +
                (f", {n_dupes} of them sharing a name with another file in the same folder (each saved by its id)" if n_dupes else ''))

    drives = shared_drives()
    if not drives:
        raise SystemExit('No shared drives were found; nothing was backed up.')
    mounts, expected = [], {}
    try:
        for d in drives:
            name = folder_name(d['name'])
            mnt = os.path.join(WORK, 'drives', name)
            if part(f"shared drive {d['name']}", lambda d=d, mnt=mnt, name=name: one_drive(d, mnt, name)):
                mounts.append(mnt)
        part('website media', website_media)
        part('admin@ mail', mail)
        part('QuickBooks', books_fresh)
        paths = [*mounts, os.path.join(WORK, 'google-files'), os.path.join(WORK, 'duplicates'), os.path.join(WORK, 'website-media'),
                 os.path.join(WORK, 'mail'), BOOKS, APP_DATA, *SECRETS]
        if part('restic backup', lambda: backup([p for p in paths if os.path.exists(p)])):
            got = snapshot_counts(mounts)
            short = [f'{os.path.basename(m)}: {got[m]} of {expected[m]}' for m in mounts if got[m] != expected[m]]
            if short:
                failed.append('snapshot is missing files: ' + '; '.join(short))
                print('FAILED', failed[-1], file=sys.stderr, flush=True)
            else:
                results.append('proof: ' + ', '.join(f'{os.path.basename(m)} {got[m]} files' for m in mounts))
    finally:
        for m in mounts:
            subprocess.run(['fusermount3', '-u', m], check=False)
    if date.today().weekday() == 6:          # Sundays: drop old snapshots, prune, and check the repository
        part('restic forget and prune', lambda: (restic('forget', '--tag', 'nightly', '--prune', *KEEP), 'old snapshots pruned')[1])
        part('restic check', lambda: (restic('check', '--read-data-subset', '2%'), 'repository checked')[1])
    if not failed:
        part('vault copy', vault)
    status(results, failed)
    if failed:
        raise SystemExit('Parts of the off-site copy failed:\n' + '\n'.join(failed))


if __name__ == '__main__':
    main()
