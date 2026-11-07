#!/bin/bash
# OCR one scanned PDF into text/<id>.txt (keeps layout-ish spacing). Usage: ocr.sh <pdf> <id>
set -e
export OMP_THREAD_LIMIT=1
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
pdftoppm -r 200 -gray -l 6 "$1" "$d/p"
for p in "$d"/p*; do tesseract "$p" - --psm 6 -c preserve_interword_spaces=1 2>/dev/null; done > "${TEXT_DIR:-$HOME/scca-data/text}/$2.txt"
