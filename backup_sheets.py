"""Nightly safety copy of the Google Sheets the app uses: exported as .xlsx into data/backups/.

The sheets in the treasurer's Drive are the record; this is a second copy on the server in case the
Drive files are damaged or deleted. Keeps the last 60 days. Run inside the container:
    docker exec event-drscca python backup_sheets.py
"""
import glob
import os
from datetime import date

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'backups')
KEEP = 60


def main():
    creds = service_account.Credentials.from_service_account_file(
        os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'], scopes=['https://www.googleapis.com/auth/drive.readonly'])
    s = AuthorizedSession(creds)
    os.makedirs(OUT, exist_ok=True)
    for label, var in (('event-registry', 'REGISTRY_SHEET_ID'), ('worker-points-ledger', 'LEDGER_SHEET_ID')):
        sheet_id = os.environ.get(var)
        if not sheet_id:
            continue
        r = s.get(f'https://www.googleapis.com/drive/v3/files/{sheet_id}/export', params={'mimeType': XLSX}, timeout=60)
        r.raise_for_status()
        path = os.path.join(OUT, f'{date.today():%Y-%m-%d}-{label}.xlsx')
        with open(path, 'wb') as f:
            f.write(r.content)
        print(f'saved {path} ({len(r.content):,} bytes)')
        for old in sorted(glob.glob(os.path.join(OUT, f'*-{label}.xlsx')))[:-KEEP]:
            os.remove(old)


if __name__ == '__main__':
    main()
