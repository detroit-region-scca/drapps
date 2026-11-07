"""Careful OCR of scanned results (PDFs with no text layer, and photos), replacing ocr.sh's first pass.

For each page: take the scan at its own resolution (or render the page at 300 dpi), enlarge low-resolution scans to
about 300 dpi, clean it up (grayscale, contrast, sharpen, black and white), turn it upright (tesseract's orientation
check), then read it as one block with the spacing kept (--psm 6), which keeps table columns apart. The text is written
to <TEXT_DIR>/<id>.txt only when it reads better than what is already there (more real words and numbers), and a
<id>.ocr.json sidecar records how it was read, so later steps can tell OCR text from a real text layer.

Usage: ocr2.py <work> <scans.json>   (scans.json: [[drive id, "pdf"|"img", name], ...]; TEXT_DIR as for the other steps)
Run with OMP_THREAD_LIMIT=1 and several processes (it uses one per file): tesseract's own threads stall otherwise.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageFilter, ImageOps

Image.MAX_IMAGE_PIXELS = None
TEXT_DIR = os.environ['TEXT_DIR']


def quality(text):
    """Share of tokens that look like words or numbers: garbage OCR is mostly symbol soup."""
    toks = re.findall(r'\S+', text)
    if not toks:
        return 0.0
    good = sum(1 for t in toks if re.fullmatch(r"[A-Za-z][a-z'.\-]+[,.]?|[A-Z]{2,}[,.]?|-?\d+(?:[.:]\d+)*[,.]?|[A-Z][a-z]*/[A-Z][a-z]*", t))
    return round(good / len(toks), 3) * min(1.0, len(toks) / 40)


def pages(path, kind, tmp):
    """Page images at their best resolution, with the dpi they were scanned at."""
    if kind == 'img':
        im = Image.open(path)
        return [(im.convert('RGB'), float((im.info.get('dpi') or (150, 150))[0] or 150))]
    out = []
    lst = subprocess.run(['pdfimages', '-list', path], capture_output=True, text=True).stdout.splitlines()[2:]
    big = [l.split() for l in lst if len(l.split()) > 13 and l.split()[2] == 'image']
    npages = int(re.search(r'Pages:\s+(\d+)', subprocess.run(['pdfinfo', path], capture_output=True, text=True).stdout or 'Pages: 1')[1])
    if big and len({b[0] for b in big}) == npages:            # one scan per page: use it at its own resolution
        subprocess.run(['pdfimages', '-png', path, os.path.join(tmp, 'p')], capture_output=True)
        for b in big:
            f = os.path.join(tmp, f'p-{int(b[1]):03d}.png')
            if os.path.exists(f):
                try:
                    dpi = int(float(b[12]))
                except ValueError:
                    dpi = 150
                out.append((Image.open(f).convert('RGB'), dpi))
    if not out:                                                 # vector or mixed pages: render at 300 dpi
        subprocess.run(['pdftoppm', '-r', '300', '-png', path, os.path.join(tmp, 'r')], capture_output=True)
        for f in sorted(x for x in os.listdir(tmp) if x.startswith('r')):
            out.append((Image.open(os.path.join(tmp, f)).convert('RGB'), 300))
    return out


def upright(im, tmp):
    f = os.path.join(tmp, 'osd.png')
    im.save(f)
    r = subprocess.run(['tesseract', f, '-', '--psm', '0'], capture_output=True, text=True)
    m = re.search(r'Rotate:\s*(\d+)', r.stdout)
    c = re.search(r'Orientation confidence:\s*([\d.]+)', r.stdout)
    if m and int(m[1]) and c and float(c[1]) >= 1.5:
        return im.rotate(-int(m[1]), expand=True), int(m[1])
    return im, 0


def clean(im, dpi):
    g = ImageOps.grayscale(im)
    scale = min(4.0, max(1.0, 300 / max(dpi, 50)))
    if scale > 1.05:
        g = g.resize((int(g.width * scale), int(g.height * scale)), Image.LANCZOS)
    g = ImageOps.autocontrast(g, cutoff=1).filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    return g.point(lambda p: 255 if p > 165 else 0), scale


def ocr_one(item):
    gid, kind, name = item
    src = os.path.join(sys.argv[1], 'files', gid)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            pgs = pages(src, kind, tmp)
        except Exception as e:
            return gid, name, None, f'unreadable: {e}'
        texts, info = [], []
        for n, (im, dpi) in enumerate(pgs):
            g, scale = clean(im, dpi)
            g, rot = upright(g, tmp)                    # on the enlarged, cleaned page: low-resolution scans fool it
            f = os.path.join(tmp, f'c{n}.png')
            g.save(f, dpi=(300, 300))
            t = subprocess.run(['tesseract', f, '-', '--psm', '6', '-c', 'preserve_interword_spaces=1'], capture_output=True, text=True).stdout
            texts.append(t)
            info.append({'page': n + 1, 'dpi': dpi, 'rotated': rot, 'scale': round(scale, 2), 'quality': quality(t)})
        text = '\n\f'.join(texts)
    out = os.path.join(TEXT_DIR, f'{gid}.txt')
    old = open(out, errors='ignore').read() if os.path.exists(out) else ''
    q_new, q_old = quality(text), quality(old)
    if q_new >= q_old or not old.strip():
        open(out, 'w').write(text)
        kept = 'new'
    else:
        kept = 'old'
    json.dump({'name': name, 'pages': info, 'quality': q_new, 'previous_quality': q_old, 'kept': kept},
              open(os.path.join(TEXT_DIR, f'{gid}.ocr.json'), 'w'))
    return gid, name, q_new, f'{kept} (was {q_old})'


if __name__ == '__main__':
    items = json.load(open(sys.argv[2]))
    with ProcessPoolExecutor(max_workers=int(os.environ.get('JOBS', '4'))) as ex:
        for gid, name, q, what in ex.map(ocr_one, items):
            print(f'{q}\t{what}\t{name}', flush=True)
