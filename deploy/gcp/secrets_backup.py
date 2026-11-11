"""Copies of what exists only on the drapps server, to the admin-only Account Recovery folder in the region shared
drive. Run as root. With these and the repo, the server can be rebuilt from scratch.

    secrets_backup.py            weekly (drscca-secrets-backup.timer): the server's secrets
    secrets_backup.py --state    nightly (drscca-state-backup.timer): the app's own files in app/data/

Secrets: /opt/drscca/data/secrets (app settings, service account keys, QuickBooks token, officials list, GAM config)
and the live timing logins and SFTP host keys, as "drapps server secrets (weekly).tar.gz".

App state: the files in /opt/drscca/app/data that cannot be rebuilt from the Google Sheets (STATE below: the audit
log, records corrections, short links for emailed votes and approvals, role settings, issued invoice numbers and
so on), as "drapps app state (nightly).tar.gz". Caches, sign-in codes, lock files, the nightly .xlsx copies and
records.db are left out. These files hold names, email addresses and working links, so they go to the same
admin-only folder as the secrets and nowhere else.

Each run updates the same Drive file, so Drive keeps the earlier versions (about 30 days of them). Workspace's
default sharing adds an "anyone at drscca.org" permission to new files; it is removed every time. Then the script
reads back who can open the file: if that is anyone but admin@ (or an address in BACKUP_READERS), the copy is still
saved but the run fails, so the alert email goes out.

The folder is ACCOUNT_RECOVERY_FOLDER_ID in drapps.env (the id is not kept in this public repo).
"""
import io
import json
import os
import sys
import tarfile

SA = os.environ.get('WORKSPACE_SA') or '/opt/drscca/data/secrets/workspace-sa.json'
ADMIN = 'admin@drscca.org'
# Account Recovery (limited to admin@): ACCOUNT_RECOVERY_FOLDER_ID in drapps.env.
FOLDER = os.environ.get('ACCOUNT_RECOVERY_FOLDER_ID') or ''
# Who may be able to open the copies, besides nobody: comma separated, lower case. Default: admin@ only.
READERS = {a.strip().lower() for a in (os.environ.get('BACKUP_READERS') or ADMIN).split(',') if a.strip()}

SECRETS_NAME = 'drapps server secrets (weekly).tar.gz'
SECRETS_PATHS = ['/opt/drscca/data/secrets', '/opt/drscca/data/livetiming/users.conf', '/opt/drscca/data/livetiming/keys']

STATE_NAME = 'drapps app state (nightly).tar.gz'
STATE_DIR = os.environ.get('APP_DATA_DIR') or '/opt/drscca/app/data'
# Named one by one, so nothing unexpected ever leaves the server. A file that does not exist is skipped.
STATE = [
    'audit.ndjson',             # who did what
    'events.ndjson',            # local mirror of the event registry
    'records-edits.json',       # Records corrections: the only copy
    'records-edits.applied',
    'short_links.json',         # short links in vote and approval emails
    'role-access.json',         # Admin > Roles
    'invoices-issued.json',     # invoice numbers already given out
    'points-saves.json',        # worker points review pages already saved
    'qbo-attached.json',        # receipts already attached in QuickBooks
    'programs.json',            # only used when the registry sheet is not
    'officials-done.json',      # results of officials changes
]

API = 'https://www.googleapis.com/drive/v3/files'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
P = {'supportsAllDrives': 'true'}


def bundle(paths):
    """A .tar.gz of these files and folders, in memory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as t:
        for p in paths:
            t.add(p, arcname=p.lstrip('/'))
    return buf.getvalue()


def state_paths(folder=None):
    folder = folder or STATE_DIR
    return [p for p in (os.path.join(folder, n) for n in STATE) if os.path.isfile(p)]


def others(permissions, allowed=None):
    """Who can open the file besides the allowed addresses: [] when it is private."""
    allowed = READERS if allowed is None else allowed
    out = []
    for p in permissions:
        if p.get('type') in ('user', 'group') and (p.get('emailAddress') or '').lower() in allowed:
            continue
        out.append(p.get('emailAddress') or p.get('domain') or p.get('type') or '?')
    return out


def session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    cr = service_account.Credentials.from_service_account_file(
        SA, scopes=['https://www.googleapis.com/auth/drive']).with_subject(ADMIN)
    return AuthorizedSession(cr)


def permissions(s, fid):
    out, token = [], None
    while True:
        params = dict(P, fields='nextPageToken,permissions(id,type,role,emailAddress,domain,permissionDetails)', pageSize=100)
        if token:
            params['pageToken'] = token
        r = s.get(f'{API}/{fid}/permissions', params=params, timeout=60)
        r.raise_for_status()
        doc = r.json()
        out += doc.get('permissions', [])
        token = doc.get('nextPageToken')
        if not token:
            return out


def upload(s, name, body, description):
    """Create the file in the folder, or put a new version on the one already there. Returns its id."""
    r = s.get(API, params=dict(P, q=f"'{FOLDER}' in parents and name = '{name}' and trashed = false",
                               includeItemsFromAllDrives='true', corpora='allDrives', fields='files(id)'), timeout=60)
    r.raise_for_status()
    found = r.json()['files']
    if found:
        fid = found[0]['id']
        r = s.patch(f'{UPLOAD}/{fid}', params=dict(P, uploadType='media'), data=body,
                    headers={'Content-Type': 'application/gzip'}, timeout=300)
        r.raise_for_status()
        return fid
    meta = json.dumps(dict(name=name, parents=[FOLDER], description=description))
    r = s.post(UPLOAD, params=dict(P, uploadType='multipart', fields='id'), timeout=300, files={
        'metadata': ('metadata', meta, 'application/json; charset=UTF-8'), 'file': (name, body, 'application/gzip')})
    r.raise_for_status()
    return r.json()['id']


def lock_down(s, fid):
    """Remove the domain-wide and public permissions set on the file itself; return who can still open it."""
    for perm in permissions(s, fid):
        if perm['type'] in ('domain', 'anyone') and not any(d.get('inherited') for d in perm.get('permissionDetails', [])):
            s.delete(f'{API}/{fid}/permissions/{perm["id"]}', params=P, timeout=60).raise_for_status()
    return permissions(s, fid)


def main(argv):
    if not FOLDER:
        sys.exit('ACCOUNT_RECOVERY_FOLDER_ID is not set in drapps.env: no copy was made.')
    if '--state' in argv[1:]:
        name, paths = STATE_NAME, state_paths()
        what = 'Nightly copy of the drapps app files that exist only on the server (audit log, records corrections, short links, role settings). See docs/RUNBOOK.md, "Backups".'
        if not paths:
            sys.exit(f'No app state files found in {STATE_DIR}: nothing was copied.')
    else:
        name, paths = SECRETS_NAME, SECRETS_PATHS
        what = 'Weekly copy of the drapps server secrets. See docs/RUNBOOK.md, "The server itself is lost".'
    body = bundle(paths)
    s = session()
    fid = upload(s, name, body, what)
    left = lock_down(s, fid)
    print(f'{name}: {len(body)} bytes, {len(paths)} paths, access: {[p.get("emailAddress") or p.get("type") for p in left]}')
    extra = others(left)
    if extra:
        sys.exit(f'{name} was saved, but it is NOT private: it can also be opened by {", ".join(sorted(set(extra)))}. '
                 f'Fix the sharing of the Account Recovery folder, or list people who are meant to have access in '
                 f'BACKUP_READERS in drapps.env.')


if __name__ == '__main__':
    main(sys.argv)
