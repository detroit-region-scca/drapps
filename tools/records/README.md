# Records build (tools/records)

Builds the database behind **Records** in DRSCCA Apps (drapps.drscca.org/records): every results, standings and
points file the region posted on its old website, 1982 to 2026, read into SQLite. It runs on any Linux computer with
Python 3, `pdftotext` and `tesseract` (poppler-utils, tesseract-ocr); only the finished `records.db` goes to the
drapps server. Nothing here holds member data: the files and the build output stay in the region's Drive.

Settings (environment): `RESULTS_BY_YEAR_FOLDER_ID` and `TIMING_RESULTS_FOLDER_ID` (Drive folder ids of *Timing and
Results / Results by year* and *Timing and Results*), `WORKSPACE_SA` (path to the region Workspace service account key,
which reads and writes the region Drive as admin@).

- **Source:** region shared drive, *Timing and Results / Results by year* (the old site's uploads, saved
  2026-10-31). The same files, sorted, are in *Timing and Results / Results by program* (Program / Year /
  `date original-name`, season points in a *Season points* subfolder). New results arrive in *Results inbox*
  (drapps Records > Add results).
- **Build data, never in this repo:** `scca.db` (everything read, including worker points sheets), `records.db` and
  `text/` (one `.txt` per file: `pdftotext -layout`, or Tesseract OCR for scans). A copy is kept in the DRSCCA Apps
  shared drive, folder *Records build data (private)*; unpack it to rebuild without redoing the OCR.

## Setup

    python3 -m venv venv && venv/bin/pip install -r tools/records/requirements.txt

Run every step with that venv's Python (`venv/bin/python -I tools/records/<step>.py ...`). The system Python may lack
`xlrd`, and then old Excel files and the zips holding them quietly read as empty.

`<data>` is the unpacked *Records build data (private)* archive: `scca.db`, `records.db`, `text/`, `work/` (plan.json,
sorted.csv, copied.json; also the `<work>` folder the steps take), `old-website-api/`. Set `TEXT_DIR=<data>/text` so the
sorting step and OCR use the saved text. After a rebuild, pack the folder again and replace the archive in Drive.

## Build order

1. `tools/records/fetch.py <work>`: list and download the Drive files; `tools/records/text.py <work>`: quick text for sorting.
2. `tools/records/ocr.sh <pdf> <id>` for scans (one thread per job: tesseract's own threads stall when several run at once).
3. `tools/records/classify.py <work>`, `tools/records/plan.py <work>`; `tools/records/drive_copy.py <work>` copies into Results by program the first time,
   `tools/records/move.py <work>` renames or moves copies after a re-sort.
   `tools/records/speedhive_import.py <work>`: the Detroit Region SCCA races at Waterford Hills from Speedhive (MYLAPS),
   saved as one CSV per race session into Results by program / Road Racing / <year> / Waterford Hills (Speedhive).
4. `tools/records/build.py <work> <data>/scca.db`: every table row (AXWare PDFs and web pages, old layouts, RallyCross
   sheets, road rally score sheets, spreadsheets, zips).
5. `tools/records/standings.py <db>`: season standings from the final points sheet of each season.
6. `tools/records/dates.py <db>`: event dates printed in the files, when the file name had none; then
   `tools/records/site_dates.py <db> <data>/old-website-api --apply`: dates from the old website's posts
   (a date near the event's name, in its year, clearly the most mentioned).
7. `tools/records/export_records.py <db> <data>/records.db`: the drapps copy (no worker points, no full text, cleaned
   event names, duplicates merged). Copy it to the VM:
   `gcloud compute scp ... drapps:/tmp/records.db` then `sudo install -o 1000 -g 1000 -m 644` into
   `/opt/drscca/app/data/records.db`. No restart needed.

Rows are read automatically, so some are wrong; the original file is linked from each event in Records.
