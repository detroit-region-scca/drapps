# DRSCCA Apps runbook

For whoever keeps drapps.drscca.org running. The README covers what the app is and how to set up a new host.
This page covers the day to day: what runs where, what to check, and what to do when something breaks.

Host-specific details (the server's name, where the secret files are, who has access) belong in a private note
in the region's DRSCCA Apps Shared Drive, never here: this repo is public.

## What runs where

| Piece | Where | Notes |
|---|---|---|
| The web app | Docker container on the host (`docker-compose.prod.yml`), 4 gunicorn workers | Restarts itself (`restart: unless-stopped`) |
| The record | Google Sheets and Drive folders in the DRSCCA Apps Shared Drive | The app is a front end to these |
| Host state | `data/` in the repo folder, mounted into the container | See below |
| Officials list | `officials.json` on the host, mounted read-only | Changed through Admin > Users |
| Host jobs | systemd user units from `deploy/` | See "Automatic jobs" |
| QuickBooks connection | a qbo-mcp install and its token store on the host | Read only |
| Code | GitHub, public | Tests run on every push |

### What is in `data/`

| File | What it is | If lost |
|---|---|---|
| `audit.ndjson` | The app's audit log: who did what, when | History is gone; the app keeps working. Back it up. |
| `role-access.json` | Admin > Roles settings | Roles fall back to defaults; redo them in Admin > Roles |
| `programs.json` | Program codes and names | Re-enter in Admin > Programs |
| `officials-queue.json`, `officials-done.json` | Officials changes waiting, and their results | Redo any change that was waiting |
| `qbo-last-run.json`, `qbo-refresh.json` | When QuickBooks last synced; a Sync now request | Rebuilt by the next sync |
| `auth_codes.json` | Sign-in codes not yet used | People request a new code |
| `sheet_cache.sqlite*` | Cache of the sheets | Rebuilt automatically |
| `backups/` | Nightly `.xlsx` copies of every sheet, 60 days | Only matters if Drive is damaged |
| `*.lock` | File locks | Safe to delete when the app is stopped |
| `events.ndjson` | Event data for the file backend only | Not used with Google Sheets |

## Automatic jobs

| Job | When | Unit | What it does |
|---|---|---|---|
| QuickBooks sync | 02:30 nightly | `drscca-qbo.timer` | Actuals, funds requests paid, bank balances, books check exceptions. On the 1st it also emails the treasurer the worker points journal entry and adds the month's automatic points. |
| Sync now | When the treasurer presses it | `drscca-qbo-refresh.path` | The same sync, on request |
| Sheet backup | 02:45 nightly | `drscca-backup.timer` | `.xlsx` copies into `data/backups/`; alerts on failure through `drscca-backup-failed.service` |
| Officials changes | When the admin saves a person in Admin > Users | `drscca-officials.path` | Updates `officials.json` and the drscca.org Google Groups with GAM |

Check them: `systemctl --user list-timers | grep drscca` and `systemctl --user status drscca-officials.path
drscca-qbo-refresh.path`. The user units only run while the user has lingering on
(`loginctl enable-linger <user>`).

**Known gap:** a failed QuickBooks sync sends no alert. The sign is the Treasurer page: "QuickBooks sync"
shows the last good run, and it should never be more than a day old.

## Routine checks

- **Weekly:** the Treasurer page shows a QuickBooks sync from last night.
- **Monthly:** `ls data/backups | tail` shows last night's files; `journalctl --user -u drscca-qbo -n 20` has no
  errors; the treasurer received the worker points email on the 1st.
- **Each election or handover:** the officials list and the groups match (`python3 tools/handover.py sync`
  without `--apply` shows any drift).

## Updating the app

    cd <repo folder>
    git pull
    OFFICIALS_HOST=/nonexistent PYTHONPATH=. venv/bin/pytest -q tests
    docker compose -f docker-compose.prod.yml up -d --build

Then sign in and open the page you changed. Check that the GitHub test run for the commit passed:
`gh run list --limit 1`. The host jobs run from the repo folder's `venv/`; after a `requirements.txt` change,
run `venv/bin/pip install -r requirements.txt` there too.

To undo a bad update: `git log --oneline -5`, `git checkout <good commit>`, rebuild as above, then fix forward
on `main`.

## Logs and restarts

| Want | Command |
|---|---|
| App errors | `docker logs --since 1h <container>` (each error starts with `Traceback`) |
| Restart the app | `docker compose -f docker-compose.prod.yml restart` |
| A job's last run | `journalctl --user -u drscca-qbo -n 50` (or `drscca-backup`, `drscca-officials`, `drscca-qbo-refresh`) |
| Run the sync by hand, changing nothing | `venv/bin/python tools/qbo_nightly.py --dry-run` |
| Who did what | the Audit page in the app, or `tail data/audit.ndjson` |

## When something breaks

**The site does not load.** Check the container is up (`docker ps`), then its log. If the container is fine,
check the HTTPS proxy and DNS in front of it. The data is safe in Drive whatever happens to the host.

**"Internal Server Error" on one page.** `docker logs` shows the traceback. Most often a sheet tab or column
was renamed or deleted by hand in Drive; put it back, or fix the code and add a test.

**Nobody receives email (sign-in codes, approvals).** Check the log for mail errors. With SMTP: the app
password was revoked or the account's password changed; make a new app password and update `.env`, then
restart. With the Gmail API: the service account's domain-wide delegation for `gmail.send` was removed in the
Workspace admin console. Also check the role group still exists and accepts mail from the sending address.

**One person is not receiving app email.** App email goes to role groups. Check the person is a member of
the group for their job (`tools/handover.py show`), and their spam folder.

**Someone cannot sign in.** They must use the address on their user in Admin > Users (officials) or on the
Volunteers page. A code works once, for 15 minutes; ask them to request a new one.

**Every page fails with a Google permission error.** The service account lost access to the Shared Drive, or
its key was deleted. Re-add it as Content manager, or make a new key in Google Cloud, replace the key file on
the host, and restart.

**QuickBooks sync fails.** `journalctl --user -u drscca-qbo -n 50`. An `HTTP 401` or a token error means the
QuickBooks connection expired: Intuit's refresh token lapses after about 100 days without use, or when someone
disconnects the app in QuickBooks. Reconnect the qbo-mcp install as a QuickBooks admin, then run Sync now.
Until then the apps work; actuals and paid status just stop updating.

**An officials change says it is still being saved.** `journalctl --user -u drscca-officials -n 50` and
`data/officials-done.json` show why. A GAM error usually means its service account lost a Workspace admin
role. Fix that, then touch the queue file to retry: `touch data/officials-queue.json`.

**The backup failed.** The sheets are still the record; only the second copy is missing. Run it by hand to
see the error: `docker exec <container> python backup_sheets.py`.

**A sheet was damaged or rows were deleted.** First use Drive's own version history (File > Version history)
on that spreadsheet. If that is not enough, the matching file in `data/backups/` is the sheet as of a past
night: open it and copy back what is missing. Do not replace the whole spreadsheet, or the file ID changes and
`.env` would need the new ID.

## Secrets

| Secret | Where | How to replace |
|---|---|---|
| `FLASK_SECRET_KEY` | `.env` | New random value, restart. Everyone is signed out. |
| Service account key | file on the host, path in `GOOGLE_SA_KEY_HOST` | New key in Google Cloud, swap the file, restart, delete the old key |
| SMTP app password (if SMTP) | `.env` | New app password, restart |
| Google OAuth client secret | `.env` | New secret on the OAuth client, restart |
| `API_KEYS` | `.env` | Generate, update the MCP client's env file, restart |
| QuickBooks tokens | qbo-mcp token store | Reconnect as above |
| `officials.json` | host file | Not secret, but private: names and addresses |

## Handing over

The new maintainer needs: an account on the host (or its replacement), push access to the GitHub repo,
Content manager on the DRSCCA Apps Shared Drive, and this page. Walk through one update and one restart
together. When the old maintainer leaves, replace the service account key and `FLASK_SECRET_KEY`, and remove
their access to the host, the repo and the drive.
