import json, os, sys, concurrent.futures as cf
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
SA = os.environ.get('WORKSPACE_SA', os.path.expanduser('~/.secrets/drscca-workspace-sa.json'))
ROOT = os.environ['RESULTS_BY_YEAR_FOLDER_ID']   # Timing and Results / Results by year
OUT = sys.argv[1]
API = 'https://www.googleapis.com/drive/v3/files'
cred = service_account.Credentials.from_service_account_file(SA, scopes=['https://www.googleapis.com/auth/drive.readonly']).with_subject('admin@drscca.org')
s = AuthorizedSession(cred)
def ls(pid):
    out, tok = [], None
    while True:
        r = s.get(API, params=dict(q=f"'{pid}' in parents and trashed=false", supportsAllDrives='true', includeItemsFromAllDrives='true',
                  pageSize=1000, pageToken=tok, fields='nextPageToken,files(id,name,mimeType,size,modifiedTime)')).json()
        out += r['files']; tok = r.get('nextPageToken')
        if not tok: return out
files = []
def walk(pid, path):
    for f in ls(pid):
        if f['mimeType'] == 'application/vnd.google-apps.folder': walk(f['id'], path + [f['name']])
        else: files.append(dict(f, path='/'.join(path)))
walk(ROOT, [])
json.dump(files, open(os.path.join(OUT, 'index.json'), 'w'), indent=1)
print(len(files), 'files')
def get(f):
    p = os.path.join(OUT, 'files', f['id'])
    if os.path.exists(p): return
    r = s.get(f"{API}/{f['id']}", params=dict(alt='media', supportsAllDrives='true')); r.raise_for_status()
    open(p, 'wb').write(r.content)
with cf.ThreadPoolExecutor(8) as ex: list(ex.map(get, files))
print('downloaded', len(os.listdir(os.path.join(OUT, 'files'))))
