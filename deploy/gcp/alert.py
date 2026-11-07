"""Email admin@ when one of the drapps VM's background jobs fails (systemd OnFailure=drscca-alert@<unit>.service).
Uses drapps' own mail sending, so it works when the home lab is down. Run as root (it reads the journal)."""
import os
import subprocess
import sys
import threading

sys.path.insert(0, '/opt/drscca/app')
# drapps.env names the key's path inside the container; on the host it is GOOGLE_SA_KEY_HOST.
os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'] = os.environ.get('GOOGLE_SA_KEY_HOST') or os.environ.get('GOOGLE_SERVICE_ACCOUNT_FILE', '')
from utils import send_mail  # noqa: E402

WHAT = {
    'drscca-backup.service': ('nightly sheet backup', 'The Google Sheets are still the record; only the nightly .xlsx copy is missing.'),
    'drscca-qbo.service': ('nightly QuickBooks sync', 'The apps keep showing the last good sync until a run succeeds.'),
    'drscca-qbo-refresh.service': ('QuickBooks refresh from the app', 'The apps keep showing the last good sync.'),
    'drscca-livetiming-archive.service': ('nightly live timing archive', 'Live timing itself still works; only the archive or its Drive copy is behind.'),
    'drscca-officials.service': ('officials-list update', 'A change made in the app was not applied to officials.json or the email groups.'),
}

unit = sys.argv[1] if len(sys.argv) > 1 else 'unknown'
name, impact = WHAT.get(unit, (unit, ''))
log = subprocess.run(['journalctl', '-u', unit, '-n', '30', '--no-pager', '-o', 'short-iso'],
                     capture_output=True, text=True).stdout
body = (f'The drapps server on Google Cloud reports that the {name} failed.\n\n{impact}\n\n'
        f'To look into it from bodhi:\n'
        f'  gcloud compute ssh drapps --zone us-central1-a --tunnel-through-iap\n'
        f'  sudo journalctl -u {unit} -n 50\n'
        f'  sudo systemctl start {unit}    (run it again)\n\n'
        f'Last 30 log lines:\n\n{log}')
send_mail(f'drapps: {name} failed', body, recipient=os.environ.get('ALERT_EMAIL', 'admin@drscca.org'))
for t in threading.enumerate():
    if t is not threading.main_thread():
        t.join(90)
