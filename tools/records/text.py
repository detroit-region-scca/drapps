import json, os, sys, logging, concurrent.futures as cf
logging.disable(logging.CRITICAL)
S = sys.argv[1]
idx = json.load(open(f'{S}/index.json'))
def one(f):
    p = f"{S}/files/{f['id']}"; n = f['name'].lower(); t = ''
    try:
        if n.endswith('.pdf'):
            from pypdf import PdfReader
            r = PdfReader(p); t = ' '.join((pg.extract_text() or '') for pg in r.pages[:2])
        elif n.endswith(('.htm', '.html', '.txt')):
            import re; t = re.sub(r'<[^>]+>', ' ', open(p, errors='ignore').read())
    except Exception as e: t = 'ERR ' + str(e)[:80]
    return f['id'], ' '.join(t.split())[:2500]
with cf.ProcessPoolExecutor(4) as ex: out = dict(ex.map(one, idx, chunksize=8))
json.dump(out, open(f'{S}/text.json', 'w'))
print(sum(1 for v in out.values() if len(v) > 40), 'with text;', sum(1 for v in out.values() if v.startswith('ERR')), 'errors')
