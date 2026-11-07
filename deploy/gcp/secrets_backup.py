"""Weekly copy of the drapps server's secrets to the admin-only Account Recovery folder in the region shared drive
(drscca-secrets-backup.timer, run as root). With this and the repo, the server can be rebuilt from scratch.

Bundles /opt/drscca/data/secrets (app settings, service account keys, QuickBooks token, officials list, GAM config)
and the live timing logins and SFTP host keys into one .tar.gz, and updates the same Drive file each week, so Drive
keeps the earlier versions. Workspace's default sharing adds an "anyone at drscca.org" permission to new files; it is
removed every time, so only admin@ can open it.
"""
import io
import json
import tarfile

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

SA = '/opt/drscca/data/secrets/workspace-sa.json'
FOLDER = '1FQpvirIhnC0tJdkueJxA-8v4g3OYkkM9'      # Account Recovery (limited to admin@)
NAME = 'drapps server secrets (weekly).tar.gz'
PATHS = ['/opt/drscca/data/secrets', '/opt/drscca/data/livetiming/users.conf', '/opt/drscca/data/livetiming/keys']
API = 'https://www.googleapis.com/drive/v3/files'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
P = {'supportsAllDrives': 'true'}

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode='w:gz') as t:
    for p in PATHS:
        t.add(p, arcname=p.lstrip('/'))
body = buf.getvalue()

cr = service_account.Credentials.from_service_account_file(
    SA, scopes=['https://www.googleapis.com/auth/drive']).with_subject('admin@drscca.org')
s = AuthorizedSession(cr)
found = s.get(API, params=dict(P, q=f"'{FOLDER}' in parents and name = '{NAME}' and trashed = false",
                               includeItemsFromAllDrives='true', corpora='allDrives', fields='files(id)')).json()['files']
if found:
    fid = found[0]['id']
    r = s.patch(f'{UPLOAD}/{fid}', params=dict(P, uploadType='media'), data=body, headers={'Content-Type': 'application/gzip'})
else:
    meta = json.dumps(dict(name=NAME, parents=[FOLDER], description='Weekly copy of the drapps server secrets. See docs/RUNBOOK.md, "The server itself is lost".'))
    r = s.post(UPLOAD, params=dict(P, uploadType='multipart', fields='id'), files={
        'metadata': ('metadata', meta, 'application/json; charset=UTF-8'), 'file': (NAME, body, 'application/gzip')})
r.raise_for_status()
fid = found[0]['id'] if found else r.json()['id']
for perm in s.get(f'{API}/{fid}/permissions', params=dict(P, fields='permissions(id,type,permissionDetails)')).json()['permissions']:
    if perm['type'] in ('domain', 'anyone') and not any(d.get('inherited') for d in perm.get('permissionDetails', [])):
        s.delete(f'{API}/{fid}/permissions/{perm["id"]}', params=P).raise_for_status()
left = [p.get('emailAddress') or p['type'] for p in s.get(f'{API}/{fid}/permissions', params=dict(P, fields='permissions(emailAddress,type)')).json()['permissions']]
print(f'{NAME}: {len(body)} bytes, access: {left}')
