# DRSCCA Apps runbook

For whoever keeps drapps.drscca.org and livetiming.drscca.org running. The README covers what the app is. This
page covers where it runs, how to change it, what runs by itself, and what to do when something breaks. The region's
whole tech stack (website, email, Drive, accounts, costs, hand-off) is in the Google Doc "DRSCCA Technology Handbook"
at the top of the DRSCCA Region shared drive.

Everything runs on one small server the region owns, in the region's Google Cloud account. Nothing depends on
anyone's home computer. This repo is public: secrets never go here, only where they live.

## What you need to maintain it

1. **The region's Google admin account** (admin@drscca.org), or your own drscca.org account with *Owner* (or
   *Compute Instance Admin* + *IAP-secured Tunnel User*) on the Google Cloud project `drscca-automation`.
2. **The Google Cloud CLI** (`gcloud`) on your computer, signed in with that account:
   `gcloud auth login` then `gcloud config set project drscca-automation`.
3. **Push access** to this GitHub repo.
4. **Content manager** on the DRSCCA Apps Shared Drive (where the data lives).

That is all. There is no server password: admin access goes through Google's sign-in.

## What runs where

| Piece | Where | Notes |
|---|---|---|
| Server | Google Cloud project `drscca-automation`, VM `drapps`, zone `us-central1-a`, e2-micro (free tier), fixed IP `drapps-ip` | Billing account "Detroit Region SCCA", $25/month budget alert |
| Everything on the server | `/opt/drscca` | `docker-compose.yml`, `Caddyfile`, copied from `deploy/gcp/` by the deploy script |
| The web app | container `drapps`, built from `/opt/drscca/app` (a clone of this repo) | Restarts itself |
| HTTPS | container `caddy` | Certificates renew themselves |
| Live timing upload | container `livetiming-sftp`, port 2222 | One login per program |
| Live timing pages | container `livetiming-web`, at livetiming.drscca.org | See "Live timing" |
| The record | Google Sheets and Drive folders in the DRSCCA Apps Shared Drive | The app is a front end to these |
| App state | `/opt/drscca/app/data/` | See below |
| Secrets | `/opt/drscca/data/secrets/` (root only; the QuickBooks token in `qbo/`, owned by drapps) | See "Secrets" |
| Host jobs | systemd units from `deploy/gcp/systemd/` | See "Automatic jobs" |
| QuickBooks | `/opt/drscca/qbo-mcp` and its one token file | The sync reads and attaches paid requests' receipts; an AI assistant runs `/opt/drscca/qbo-mcp.sh` over the IAP tunnel as admin@ (drscca.org's Google Cloud session control never forces a new sign-in) |
| DNS | drscca.org zone at WordPress.com: `drapps`, `drevent`, `livetiming` A records | Point at the fixed IP |
| Code | GitHub `detroit-region-scca/drapps`, public | Tests run on every push |

The server only accepts web (80/443) and live timing uploads (2222) from the internet. Admin SSH (22) is
allowed only from Google's IAP range, so you connect through Google:

    gcloud compute ssh drapps --zone us-central1-a --tunnel-through-iap

Or open the VM list in the Cloud console and press **SSH**.

### What is in `data/`

| File | What it is | If lost |
|---|---|---|
| `audit.ndjson` | The app's audit log: who did what, when | History is gone; the app keeps working |
| `role-access.json` | Admin > Roles settings | Roles fall back to defaults; redo them in Admin > Roles |
| `programs.json` | Program codes and names | Re-enter in Admin > Programs |
| `short_links.json` | Short links in emails (votes, approvals) | Old emailed links stop working |
| `officials-queue.json`, `officials-done.json` | Officials changes waiting, and their results | Redo any change that was waiting |
| `qbo-last-run.json`, `qbo-refresh.json` | When QuickBooks last synced; a Sync now request | Rebuilt by the next sync |
| `auth_codes.json` | Sign-in codes not yet used | People request a new code |
| `sheet_cache.sqlite*` | Cache of the sheets | Rebuilt automatically |
| `backups/` | Nightly `.xlsx` copies of every sheet, 60 days | Only matters if Drive is damaged |

The files in `data/` belong to the `drapps` user (uid 1000), the same user the container runs as.

## Changing the app

On your computer, in this repo:

    OFFICIALS_HOST=/nonexistent PYTHONPATH=. venv/bin/pytest -q tests     # tests must pass
    git commit ...
    deploy/gcp/deploy.sh

The deploy script runs the tests, refuses uncommitted changes, pushes, then on the server pulls the code,
copies the compose file, Caddyfile, job units and live timing pages into place, rebuilds and restarts drapps,
reloads Caddy and checks the site answers. Signed-in people stay signed in. Then check the GitHub test run:
`gh run list --limit 1`.

To undo a bad update: `git revert` the commit and deploy again.

Server-only changes (secrets, `officials.json` by hand) are made on the server, never in git; restart with
`sudo docker compose restart drapps` in `/opt/drscca`.

## Automatic jobs

All run on the server. Any failure emails admin@drscca.org (from apps@) with the last log lines
(`drscca-alert@.service`, `deploy/gcp/alert.py`).

| Job | When (Detroit time) | Unit | What it does |
|---|---|---|---|
| QuickBooks sync | 02:30 nightly | `drscca-qbo.timer` | Actuals, funds requests paid, bank balances, balance sheet, money owed, books check; attaches paid requests' receipts in QuickBooks (`data/qbo-attached.json` remembers which receipts went). On the 1st it also emails the treasurer the worker points journal entry and adds the month's automatic points. |
| Sync now | When the treasurer presses it | `drscca-qbo-refresh.path` | The same sync, on request |
| Sheet backup | 02:45 nightly | `drscca-backup.timer` | `.xlsx` copies into `data/backups/` |
| Officials changes | When the admin saves a person in Admin > Users | `drscca-officials.path` | Updates `officials.json` and the drscca.org Google Groups with GAM |
| Software updates | Sundays 04:30 | `drscca-update.timer` | Newest Caddy, nginx and SFTP images, drapps rebuilt on the newest Python, newest GAM and libraries |
| System updates | daily | `unattended-upgrades` | Debian, Docker, Node and Google packages; reboots at 04:00 only when an update needs it |
| Secrets copy | Sundays 05:00 | `drscca-secrets-backup.timer` | Updates "drapps server secrets (weekly).tar.gz" in the admin-only Account Recovery folder (Drive keeps earlier versions) |

**Outside the server:** Google Cloud uptime checks look at drapps.drscca.org and livetiming.drscca.org every 5
minutes from three US locations, and email admin@ ("... is down") if two of them fail for 10 minutes (Cloud
console > Monitoring > Uptime checks and Alerting). These keep working even if the server is completely off.

**Protection:** fail2ban blocks an address for an hour after 5 wrong live timing passwords in 10 minutes
(`sudo fail2ban-client status livetiming-sftp`; to lift a ban, `sudo fail2ban-client set livetiming-sftp unbanip
<address>`). Container logs are capped at 3 x 10 MB each.

Check them: `systemctl list-timers 'drscca-*'` and `systemctl status drscca-officials.path
drscca-qbo-refresh.path`.

## Live timing

AXWare on each program's timing laptop uploads its live results every minute over SFTP:

| AXWare setting (Setup > Configuration Settings > Live Results) | Value |
|---|---|
| Host Name | livetiming.drscca.org |
| Port # | 2222 |
| File Protocol | SFTP |
| User Name | `autocross` or `rallycross` |
| Upload Location | /livetiming |
| Public Browse URL | `autocross/` or `rallycross/` |

Each login can only write its own program's folder (`/opt/drscca/data/livetiming/www/<program>/`); nothing
else on the server is reachable. Every upload replaces the last; nothing is kept between events. The official results are the
PDFs each program posts after an event. The front page links both programs.

**Logins** are in `/opt/drscca/data/livetiming/users.conf` (hashed). To change a password:
`openssl passwd -6`, put the hash in that line, then `sudo docker compose restart livetiming-sftp`. To add a
program, add a line (new uid), a folder owned by that uid, and a volume line in `docker-compose.yml`.

## Records

`/records` shows region results back to 1982 from `data/records.db`, a SQLite file built off the server by
`tools/records/` (see its README) and copied in with `gcloud compute scp` and `sudo install -o 1000 -g 1000 -m 644`.
No restart is needed.

**Corrections.** A program's director (Admin > Officials) and the officers see "Correct this event" and "Correct the
standings" in Records. Each correction is applied at once, added to `data/records-edits.json` and written to the audit
log. A newly copied `records.db` gets the whole log applied on the first page view (`records-edits.applied` remembers
which file it was applied to), and the build applies it too: copy `records-edits.json` off the server before each
export. Never delete that file: it holds people's corrections. The nightly backup (`backup_sheets.py`) keeps 60 dated copies in `data/backups/`. New results that people add land in the Drive folder *Timing and Results / Results inbox*
(`RESULTS_INBOX_FOLDER_ID`); they are read in later, in a batch. Records never holds worker points.

Worker points: Add points (typed or the uploaded worker points sheet) shows a review page first; Save there is the only
write. Each review page saves once (`data/points-saves.json` remembers saved pages), and the duplicate check runs again
under a file lock on a fresh read, so two people sending the same names for an event cannot double-count them.

## Logs and restarts

| Want | Command (on the server, in `/opt/drscca`) |
|---|---|
| What is running | `sudo docker compose ps` |
| App errors | `sudo docker logs --since 1h drapps` (each error starts with `Traceback`) |
| Restart the app | `sudo docker compose restart drapps` |
| A job's last run | `sudo journalctl -u drscca-qbo -n 50` (or `drscca-backup`, `drscca-officials`, `drscca-update`) |
| Run a job now | `sudo systemctl start drscca-backup.service` |
| Who did what | the Audit page in the app, or `tail app/data/audit.ndjson` |

## When something breaks

**The site does not load.** `sudo docker compose ps`, then the container's log. If the VM itself is down,
start it in the Cloud console (Compute Engine > VM instances). The data is safe in Drive whatever happens to the
server.

**"Internal Server Error" on one page.** `sudo docker logs drapps` shows the traceback. Most often a sheet tab or
column was renamed or deleted by hand in Drive; put it back, or fix the code and add a test.

**Nobody receives email.** The app sends through the Gmail API as the service account. Check the log for mail
errors; the usual cause is the service account's domain-wide delegation for Gmail being removed in the
Workspace admin console.

**One person is not receiving app email.** App email goes to role groups. Check the person is a member of the
group for their job (`tools/handover.py show`), and their spam folder.

**Someone cannot sign in.** They must use the address on their user in Admin > Users (officials) or on the App
access page. A code works once, for 15 minutes; ask them to request a new one.

**Every page fails with a Google permission error.** The service account lost access to the Shared Drive, or its
key was deleted. Re-add it as Content manager, or make a new key, replace `drapps-sa.json`, restart.

**QuickBooks sync fails.** `sudo journalctl -u drscca-qbo -n 50`. A 401 or token error means the QuickBooks
connection expired (about 100 days without use, or someone disconnected the app in QuickBooks). Reconnect as a
QuickBooks admin and put the new refresh token in `qbo/qbo.env`. QuickBooks replaces the token every time it is
used, so there must only ever be one copy of `qbo.env`: the one on the server. Its folder `qbo/` belongs to
drapps so the new token can be saved; if drapps cannot write there the token goes stale within a day.

**An officials change says it is still being saved.** `sudo journalctl -u drscca-officials -n 50` and
`data/officials-done.json` show why. A GAM error usually means its service account lost a Workspace admin role.
Fix that, then `sudo touch app/data/officials-queue.json` to retry.

**A sheet was damaged or rows were deleted.** First use Drive's version history on that spreadsheet. If that is
not enough, the matching file in `data/backups/` is the sheet as of a past night: copy back what is missing. Do
not replace the whole spreadsheet, or its ID changes.

**The server itself is lost or broken.** The disk is snapshotted daily, 7 days kept (snapshot schedule
`daily-7`): in the Cloud console, create a disk from the latest snapshot and a VM from that disk with the same
network tag `drapps` and the IP `drapps-ip`. To build from scratch instead: Debian 12 VM, Docker, clone this repo
to `/opt/drscca/app`, copy `deploy/gcp/` into place, restore `/opt/drscca/data/secrets/` from the Account
Recovery folder, `sudo docker compose up -d`, install the units, then run `deploy/gcp/deploy.sh`.

## Secrets

All on the server in `/opt/drscca/data/secrets/` (root only), never in git. A copy of anything you cannot
recreate belongs in the admin-only Account Recovery folder in the region shared drive.

| Secret | File | How to replace |
|---|---|---|
| App settings incl. `FLASK_SECRET_KEY`, `API_KEYS` | `drapps.env` | Edit, restart drapps. A new `FLASK_SECRET_KEY` signs everyone out and voids emailed links. |
| App service account key | `drapps-sa.json` | New key in Google Cloud, swap the file, restart, delete the old key |
| Workspace service account key (GAM, secrets copy) | `workspace-sa.json`, `gam/gam.cfg` | New key, swap the file |
| QuickBooks token | `qbo/qbo.env` | Reconnect as above |
| Live timing logins | `/opt/drscca/data/livetiming/users.conf` | See "Live timing" |
| Officials list | `officials.json` | Not secret, but private: names and addresses |

## Handing over

The new maintainer needs the four things in "What you need", and this page. Walk through one deploy and one
restart together. When a maintainer leaves: remove their access to the Google Cloud project, the repo and the
drive, and replace `FLASK_SECRET_KEY`, the service account keys and the API key they used.
