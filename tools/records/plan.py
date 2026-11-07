import csv, hashlib, json, os, re, sys, collections
S = sys.argv[1]
rows = list(csv.DictReader(open(f'{S}/sorted.csv')))
plan, seen = [], set()
for r in rows:
    prog = r['program']; yr = r['year'] or 'Year unknown'
    path = [prog] if prog == 'Not sorted' else [prog, yr] + (['Season points'] if r['kind'] == 'points' else [])
    name = r['name']
    if r['date'] and not name.startswith(r['date']): name = f"{r['date']} {name}"
    h = hashlib.md5(open(f"{S}/files/{r['id']}", 'rb').read()).hexdigest()
    key = ('/'.join(path), h)
    if key in seen: continue          # exact duplicate already going to that folder
    seen.add(key)
    plan.append(dict(id=r['id'], folder='/'.join(path), name=name, old=r['oldpath'] + '/' + r['name']))
json.dump(plan, open(f'{S}/plan.json', 'w'), indent=1)
print(len(plan), 'files to copy,', len(rows) - len(plan), 'exact duplicates skipped')
c = collections.Counter(p['folder'].split('/')[0] for p in plan); print(dict(c))
yrs = collections.defaultdict(set)
for p in plan:
    f = p['folder'].split('/')
    if len(f) > 1: yrs[f[0]].add(f[1])
for k, v in yrs.items(): print(k, min(v), '-', max(x for x in v if x[0].isdigit()), len(v), 'years')
