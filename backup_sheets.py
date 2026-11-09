"""Nightly safety copy of the Google Sheets the app uses: exported as .xlsx into data/backups/. Also a dated copy of
data/records-edits.json, the Records corrections log (it lives only on the server).

The sheets in the treasurer's Drive are the record; this is a second copy on the server in case the
Drive files are damaged or deleted. Keeps the last 60 days. Run inside the container (deploy/gcp/systemd/drscca-backup.* runs it nightly):
    docker exec <container> python backup_sheets.py

Each sheet is tried by itself: one that fails does not stop the others. If any failed, the run ends with a non-zero
exit so the job's alert email goes out.
"""
import glob
import os
import shutil
import sys
from datetime import date

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'backups')
KEEP = 60
# Every spreadsheet the apps use; any that is not configured is skipped.
SHEETS = (('event-registry', 'REGISTRY_SHEET_ID'), ('drscca-members', 'MEMBERS_SHEET_ID'), ('drscca-org-public', 'PUBLIC_SHEET_ID'),
          ('event-budgets', 'BUDGETS_SHEET_ID'), ('funds-requests', 'FUNDS_SHEET_ID'), ('annual-plan', 'PLAN_SHEET_ID'),
          ('operations', 'OPERATIONS_SHEET_ID'), ('invoices', 'INVOICES_SHEET_ID'))


def save_sheet(s, label, sheet_id):
    r = s.get(f'https://www.googleapis.com/drive/v3/files/{sheet_id}/export', params={'mimeType': XLSX}, timeout=60)
    r.raise_for_status()
    path = os.path.join(OUT, f'{date.today():%Y-%m-%d}-{label}.xlsx')
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(r.content)
    os.replace(tmp, path)       # a half-written file never takes the place of a good one
    print(f'saved {path} ({len(r.content):,} bytes)')
    for old in sorted(glob.glob(os.path.join(OUT, f'*-{label}.xlsx')))[:-KEEP]:
        os.remove(old)


def save_records_edits():
    edits = os.path.join(os.path.dirname(OUT), 'records-edits.json')
    if not os.path.exists(edits):
        return
    path = os.path.join(OUT, f'{date.today():%Y-%m-%d}-records-edits.json')
    shutil.copy(edits, path)
    print(f'saved {path}')
    for old in sorted(glob.glob(os.path.join(OUT, '*-records-edits.json')))[:-KEEP]:
        os.remove(old)


def main():
    """Returns the labels that could not be saved (empty when all went well)."""
    os.makedirs(OUT, exist_ok=True)
    failed = []
    try:
        creds = service_account.Credentials.from_service_account_file(
            os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], scopes=['https://www.googleapis.com/auth/drive.readonly'])
        s = AuthorizedSession(creds)
    except Exception as e:      # noqa: BLE001  no key: no sheet can be saved, but the records corrections still can
        print(f'FAILED every sheet: cannot use the service account key: {type(e).__name__}: {e}')
        failed.append('service account key')
        s = None
    for label, var in SHEETS:
        sheet_id = os.environ.get(var)
        if not sheet_id or s is None:
            continue
        try:
            save_sheet(s, label, sheet_id)
        except Exception as e:      # noqa: BLE001  one bad sheet must not stop the rest
            print(f'FAILED {label} ({var}): {type(e).__name__}: {e}')
            failed.append(label)
    try:
        save_records_edits()
    except Exception as e:      # noqa: BLE001
        print(f'FAILED records-edits.json: {type(e).__name__}: {e}')
        failed.append('records-edits.json')
    return failed


if __name__ == '__main__':
    failed = main()
    if failed:
        sys.exit(f'Sheet backup: {len(failed)} not saved: {", ".join(failed)}')
