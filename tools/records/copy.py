import json, os, sys, threading, concurrent.futures as cf
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
S = sys.argv[1]
PARENT = os.environ['TIMING_RESULTS_FOLDER_ID']   # Timing and Results
API = 'https://www.googleapis.com/drive/v3/files'
cred = service_account.Credentials.from_service_account_file(os.environ.get('WORKSPACE_SA', os.path.expanduser('~/.secrets/drscca-workspace-sa.json')),
        scopes=['https://www.googleapis.com/auth/drive']).with_subject('admin@drscca.org')
s = AuthorizedSession(cred)
P = dict(supportsAllDrives='true')
def find_or_make(name, parent):
    q = f"'{parent}' in parents and trashed=false and mimeType='application/vnd.google-apps.folder' and name='{name}'"
    f = s.get(API, params=dict(P, q=q, includeItemsFromAllDrives='true', fields='files(id)')).json()['files']
    if f: return f[0]['id']
    r = s.post(API, params=P, json=dict(name=name, parents=[parent], mimeType='application/vnd.google-apps.folder')); r.raise_for_status()
    return r.json()['id']
plan = json.load(open(f'{S}/plan.json'))
done_p = f'{S}/copied.json'
done = json.load(open(done_p)) if os.path.exists(done_p) else {}
root = find_or_make('Results by program', PARENT)
folders = {}
for path in sorted({p['folder'] for p in plan}):
    cur, acc = root, []
    for part in path.split('/'):
        acc.append(part); k = '/'.join(acc)
        if k not in folders: folders[k] = find_or_make(part, cur)
        cur = folders[k]
lock = threading.Lock()
def cp(p):
    if p['id'] in done: return
    for i in range(5):
        r = s.post(f"{API}/{p['id']}/copy", params=dict(P, fields='id'), json=dict(name=p['name'], parents=[folders[p['folder']]]))
        if r.status_code < 400: break
        import time; time.sleep(2 ** i)
    r.raise_for_status()
    with lock:
        done[p['id']] = r.json()['id']
        if len(done) % 100 == 0: json.dump(done, open(done_p, 'w'))
with cf.ThreadPoolExecutor(6) as ex: list(ex.map(cp, plan))
json.dump(done, open(done_p, 'w'))
print('copied', len(done), 'of', len(plan), '; root', root)
