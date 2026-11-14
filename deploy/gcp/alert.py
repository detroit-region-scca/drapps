"""Email admin@ when one of the drapps VM's background jobs fails (systemd OnFailure=drscca-alert@<unit>.service).
Uses drapps' own mail sending, so it works when the home lab is down. Run as root (it reads the journal).

The email is sent before this script ends, and the script ends with a non-zero exit if it could not be sent, so a
lost alert shows in `systemctl --failed` and `journalctl -u 'drscca-alert@*'`.
Test it: sudo systemctl start drscca-alert@test.service   (an email "drapps: test failed" should arrive)."""
import logging
import os
import subprocess
import sys

sys.path.insert(0, '/opt/drscca/app')
# drapps.env names the key's path inside the container; on the host it is GOOGLE_SA_KEY_HOST (set in the unit).
os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'] = os.environ.get('GOOGLE_SA_KEY_HOST') or os.environ.get('GOOGLE_SERVICE_ACCOUNT_FILE', '')

# unit: (what to call it, what its failure means)
WHAT = {
    'drscca-backup.service': ('nightly sheet backup', 'The Google Sheets are still the record; only the nightly .xlsx copy of one or more sheets is missing. The log lines below name each one.'),
    'drscca-qbo.service': ('nightly QuickBooks sync', 'Part or all of the sync did not run; the log lines below say which part. The apps keep showing the last good numbers until a run succeeds.'),
    'drscca-qbo-refresh.service': ('QuickBooks refresh from the app', 'Part or all of the sync did not run; the log lines below say which part. The apps keep showing the last good numbers.'),
    'drscca-update.service': ('weekly software update', 'If the new drapps image failed its tests or did not start, the previous one is still running; the log lines below say so. Check that every container is up.'),
    'drscca-secrets-backup.service': ('weekly secrets copy to Drive', 'Nothing is down. Either the off-site copy of the server settings is out of date, or it was saved but someone other than admin@ can open it; the log lines below say which.'),
    'drscca-site-backup.service': ('weekly drscca.org website copy', 'Nothing is down; this week\'s copy of the website\'s content was not saved. The earlier copies are still in the region drive.'),
    'drscca-officials.service': ('officials-list update', 'Either a change made in the app was not applied to officials.json, or it was applied and its readable copy in Drive was not updated; the log lines below say which.'),
    'drscca-groups.service': ('email groups refresh', 'The Email groups page could not read the groups from Google.'),
    'drscca-offsite-backup.service': ('off-site copy', 'Part of last night\'s copy to Cloud Storage did not finish; the parts that did are saved. The log lines below name each part that failed.'),
    'drscca-offsite-check.service': ('off-site copy check', 'No complete off-site copy has finished in the last 36 hours. Look at drscca-offsite-backup\'s last run.'),
    'drscca-hassles-subscribe.service': ('Hassles subscribe', 'A Subscribe or Unsubscribe on the Hassles page may not have reached hassles@drscca.org; the log lines below say which address.'),
}


class _Sent(logging.Handler):
    """utils.send_mail reports through its logger and never raises: remember what it said."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.sent, self.errors = False, []

    def emit(self, record):
        text = record.getMessage()
        if record.levelno >= logging.ERROR:
            self.errors.append(text)
        elif text.startswith('Email sent'):
            self.sent = True


class _Inline:
    """Stands in for threading.Thread inside utils: the mail is sent here and now, not in the background."""

    def __init__(self, target=None, args=(), kwargs=None, **_):
        self._run = lambda: target(*args, **(kwargs or {}))

    def start(self):
        self._run()

    def join(self, timeout=None):
        pass


def send(subject, body, recipient):
    """Send one email and wait for it. Returns '' when it went, else why it did not."""
    import utils
    seen = _Sent()
    log = logging.getLogger(utils.__name__)
    old_level = log.level
    log.addHandler(seen)
    log.setLevel(logging.INFO)
    real = utils.threading
    utils.threading = type('threading', (), {'Thread': _Inline})
    try:
        utils.send_mail(subject, body, recipient=recipient)
    except Exception as e:      # noqa: BLE001
        seen.errors.append(f'{type(e).__name__}: {e}')
    finally:
        utils.threading = real
        log.removeHandler(seen)
        log.setLevel(old_level)
    if seen.sent and not seen.errors:
        return ''
    return '; '.join(seen.errors) or 'the mail code did not report that the email was sent'


def main(argv):
    unit = argv[1] if len(argv) > 1 else 'unknown'
    name, impact = WHAT.get(unit, (unit, ''))
    log = subprocess.run(['journalctl', '-u', unit, '-n', '30', '--no-pager', '-o', 'short-iso'],
                         capture_output=True, text=True).stdout
    body = (f'The drapps server on Google Cloud reports that the {name} failed.\n\n{impact}\n\n'
            f'To look into it from bodhi:\n'
            f'  gcloud compute ssh drapps --zone us-central1-a --tunnel-through-iap\n'
            f'  sudo journalctl -u {unit} -n 50\n'
            f'  sudo systemctl start {unit}    (run it again)\n\n'
            f'Last 30 log lines:\n\n{log}')
    recipient = os.environ.get('ALERT_EMAIL', 'admin@drscca.org')
    why = send(f'drapps: {name} failed', body, recipient)
    if why:
        print(f'ALERT NOT SENT to {recipient} about {unit}: {why}', file=sys.stderr)
        return 1
    print(f'Alert sent to {recipient} about {unit}.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
