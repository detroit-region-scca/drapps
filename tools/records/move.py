"""Apply re-sorts to the Drive copies in Results by program. Usage: move.py <work>  (reads moves.json, copied.json)"""
import json, os, sys
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
S = sys.argv[1]; API = 'https://www.googleapis.com/drive/v3/files'; P = dict(supportsAllDrives='true')
s = AuthorizedSession(service_account.Credentials.from_service_account_file(os.environ.get('WORKSPACE_SA', os.path.expanduser('~/.secrets/drscca-workspace-sa.json')),
        scopes=['https://www.googleapis.com/auth/drive']).with_subject('admin@drscca.org'))
def folder(name, parent):
    q = f"'{parent}' in parents and trashed=false and mimeType='application/vnd.google-apps.folder' and name='{name}'"
    f = s.get(API, params=dict(P, q=q, includeItemsFromAllDrives='true', fields='files(id)')).json()['files']
    if f: return f[0]['id']
    r = s.post(API, params=P, json=dict(name=name, parents=[parent], mimeType='application/vnd.google-apps.folder')); r.raise_for_status(); return r.json()['id']
root = folder('Results by program', os.environ['TIMING_RESULTS_FOLDER_ID'])
copies = json.load(open(f'{S}/copied.json'))
for fid, old, new in json.load(open(f'{S}/moves.json')):
    cid = copies[fid]; parts = new.split('/'); dest = root
    for p in parts[:-1]: dest = folder(p, dest)
    cur = s.get(f'{API}/{cid}', params=dict(P, fields='parents')).json()['parents']
    r = s.patch(f'{API}/{cid}', params=dict(P, addParents=dest, removeParents=','.join(cur)), json=dict(name=parts[-1])); r.raise_for_status()
    print('moved', new)
