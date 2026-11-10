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
| The web app | container `drapps`, image `drapps:latest`, built from `/opt/drscca/app` (a clone of this repo) | Restarts itself. `drapps:previous` is the version before, kept for a rollback |
| HTTPS | container `caddy` | Certificates renew themselves. Adds the security headers and hides the server name (`deploy/gcp/Caddyfile`) |
| Live timing upload | container `livetiming-sftp`, port 2222 | One login per program |
| Live timing pages | container `livetiming-web`, at livetiming.drscca.org | See "Live timing" |
| The record | Google Sheets and Drive folders in the DRSCCA Apps Shared Drive | The app is a front end to these |
| App state | `/opt/drscca/app/data/` | See below |
| Secrets | `/opt/drscca/data/secrets/` (root only; the QuickBooks token in `qbo/`, owned by drapps) | See "Secrets" |
| Host jobs | systemd units from `deploy/gcp/systemd/`, run with the Python in `/opt/drscca/venv` | See "Automatic jobs" |
| QuickBooks | `/opt/drscca/qbo-mcp` and its one token file | The sync reads and attaches paid requests' receipts; an AI assistant runs `/opt/drscca/qbo-mcp.sh` over the IAP tunnel as admin@ (drscca.org's Google Cloud session control never forces a new sign-in) |
| DNS | drscca.org zone at WordPress.com: `drapps`, `drevent`, `livetiming` A records | Point at the fixed IP |
| Code | GitHub `detroit-region-scca/drapps`, public | Tests run on every push |

The server only accepts web (80/443) and live timing uploads (2222) from the internet. Admin SSH (22) is
allowed only from Google's IAP range, so you connect through Google:

    gcloud compute ssh drapps --zone us-central1-a --tunnel-through-iap

Or open the VM list in the Cloud console and press **SSH**.

### What is in `data/`

This is `/opt/drscca/app/data/` on the server. "Off-server copy" means the file is in the nightly
`drapps app state (nightly).tar.gz` in the admin-only Account Recovery folder (see "Backups").

| File | What it is | Off-server copy | If lost |
|---|---|---|---|
| `audit.ndjson` | The app's audit log: who did what, when. Each entry is also written to the Audit tab of the registry spreadsheet | Yes | The Audit tab still has it; the app keeps working |
| `events.ndjson` | Local copy of the event registry, written with every change. The Events tab of the registry spreadsheet is the record | Yes | Nothing: the sheet is the record |
| `records-edits.json`, `records-edits.applied` | Corrections people made in Records, and which `records.db` they were last applied to. The only copy of those corrections | Yes, and 60 dated copies in `backups/` | Corrections are gone unless restored. Never delete it |
| `records.db` | The Records database (results back to 1982), built off the server by `tools/records/` | No (rebuilt off the server) | Records shows nothing until it is copied in again; see "Records" |
| `records-base.db`, `records-base.json` | A copy of `records.db` as it arrived, before corrections, and which corrections it may already hold. Undo on the Corrections page rebuilds from it | No | Safe to delete: it is remade from the current database on the next page view |
| `role-access.json` | Admin > Roles settings | Yes | Roles fall back to defaults; redo them in Admin > Roles |
| `short_links.json` | Short links in emails (votes, approvals) | Yes | Emailed links not yet used stop working; send them again |
| `invoices-issued.json` | Invoice numbers already given out, and New invoice pages already used | Yes | A number could be reused after the newest invoice is deleted; a double click could make two invoices |
| `points-saves.json` | Worker points review pages already saved | Yes | An old review page left open could be saved a second time |
| `qbo-attached.json` | Which receipts the sync already attached in QuickBooks | Yes | The next sync may attach the same receipts again |
| `officials-queue.json`, `officials-done.json` | Officials changes waiting, and the results of the last 50 | `officials-done.json` only | Redo any change that was waiting |
| `programs.json` | Program codes and names for the file backend. On the server the Programs tab of the registry spreadsheet is the record, and this file is only read if Google cannot be reached. Check on the server whether it exists | Yes, if it exists | Nothing on the server |
| `qbo-last-run.json`, `qbo-refresh.json` | When QuickBooks last synced and what it said; a Sync now request | No | Rebuilt by the next sync |
| `email-groups.json`, `groups-refresh` | The Email groups page's list; a Refresh request | No | Press Refresh on the Email groups page |
| `msr-cache.json` | MotorsportReg pages read in the last few hours | No | Rebuilt automatically |
| `auth_codes.json` | Sign-in codes not yet used | No (on purpose) | People request a new code |
| `sheet_cache.sqlite*`, `sheet_cache.leader.lock` | Cache of the sheets | No | Rebuilt automatically |
| `*.lock`, `*.tmp` | Lock files and half-written files | No | Nothing |
| `backups/` | Nightly `.xlsx` copies of every sheet and dated copies of `records-edits.json`, 60 days | No (the sheets are in Drive) | Only matters if Drive is damaged |

The files in `data/` belong to the `drapps` user (uid 1000), the same user the container runs as. A file owned
by root there can stop the app from writing it: `sudo chown 1000:1000 <file>`.

## Changing the app

On your computer, in this repo:

    OFFICIALS_HOST=/nonexistent PYTHONPATH=. venv/bin/pytest -q tests     # tests must pass
    git commit ...
    deploy/gcp/deploy.sh

What the deploy does, in order. It stops at the first failure, shows the error, and ends with a non-zero exit.

1. **On your computer** (`deploy.sh`): refuses uncommitted changes, runs the tests, pushes. If a test fails,
   nothing is pushed.
2. **On the server, before anything live changes** (`on-server.sh`): moves `/opt/drscca/app` to exactly the
   commit you tested, builds the image `drapps:candidate`, and runs the tests again inside that image (no
   network, no server data). If the build or a test fails, the code on the server goes back to the commit it
   was on and the deploy stops. The site was never touched.
3. **Start it:** the image that is running is tagged `drapps:previous`, the new one is tagged `drapps:latest`
   and started. The server then asks the new container for its home page, from inside the container, and checks
   that the container reports the commit you deployed. Signed-in people stay signed in.
4. **If it does not answer in about 90 seconds: automatic rollback.** The server starts `drapps:previous`
   again, puts back the compose file and the code, and prints a banner:
   `DEPLOY FAILED AND WAS ROLLED BACK`. The log lines of the failed start are printed above the banner. The site
   is down for the time the new version was trying to start, about a minute and a half at most.
5. **Only when the new version answers,** the rest of the server setup: the Caddyfile (checked by Caddy before
   it is put in place; the old one comes back if Caddy refuses it), live timing pages, the job units, Python
   libraries for the host jobs (`pip install -r requirements.txt` into `/opt/drscca/venv`), the other
   containers, old images and build cache cleared. If one of these fails the banner says
   `DEPLOY NOT FINISHED` and names the step. The app stays on the new version; fix the cause and deploy again.
6. **Back on your computer:** the deploy checks the server reported your commit as deployed, then that
   https://drapps.drscca.org answers 200 and sends the security headers.

The last line is `Deployed <commit>.` If you do not see it, the deploy did not finish. Then check the GitHub test
run: `gh run list --limit 1`.

**Job units.** Every `.timer` and `.path` file in `deploy/gcp/systemd/` is turned on by every deploy, so a new
job only needs its files added there. A `drscca-*` unit on the server that is no longer in the repo is stopped,
turned off and removed (a copy goes to `/opt/drscca/data/removed-units/`). Only files named `drscca-*.service`,
`drscca-*.timer` or `drscca-*.path` are ever removed, so do not give a hand-made unit on the server a `drscca-`
name: add it to the repo instead.

**To undo a bad update that did start:** `git revert` the commit and deploy again. To go back at once without a
deploy, on the server in `/opt/drscca`:

    sudo docker tag drapps:previous drapps:latest && sudo docker compose up -d --no-build drapps

That runs the previous image until the next deploy. The code in `/opt/drscca/app` (used by the host jobs) stays
on the newer commit until then, so follow it with a revert and a deploy.

Server-only changes (secrets, `officials.json` by hand) are made on the server, never in git; restart with
`sudo docker compose restart drapps` in `/opt/drscca`.

The app runs under gunicorn with 2 workers of 4 threads each; calls to Google give up after 110 seconds
(`deploy/gcp/docker-compose.yml`).

## Automatic jobs

All run on the server. A job that ends with an error emails admin@drscca.org (from apps@) with the last log
lines (`drscca-alert@.service`, `deploy/gcp/alert.py`). The QuickBooks sync counts as failed when any part of it
fails; the sheet backup when any one sheet could not be saved. Two limits: a job that hangs without ending
sends nothing, and if the email itself cannot be sent the only trace is on the server
(`systemctl --failed`, `sudo journalctl -u 'drscca-alert@*' -n 30`). To test the email:
`sudo systemctl start drscca-alert@test.service` should deliver "drapps: test failed" to admin@.

| Job | When (Detroit time) | Unit | What it does |
|---|---|---|---|
| QuickBooks sync | 02:30 nightly | `drscca-qbo.timer` | Actuals, funds requests paid, bank balances, balance sheet, money owed, books check; attaches paid requests' receipts in QuickBooks (`data/qbo-attached.json` remembers which receipts went). On the 1st it also emails the treasurer the worker points journal entry and adds the month's automatic points. |
| Sync now | When the treasurer presses it | `drscca-qbo-refresh.path` | The same sync, on request |
| Sheet backup | 02:45 nightly | `drscca-backup.timer` | `.xlsx` copies into `data/backups/`, each sheet by itself, and a dated copy of `records-edits.json` |
| App state copy | 03:00 nightly | `drscca-state-backup.timer` | `deploy/gcp/secrets_backup.py --state` updates "drapps app state (nightly).tar.gz" in the admin-only Account Recovery folder: the files marked "Off-server copy" in the `data/` table |
| Officials changes | When the admin saves a person in Admin > Users | `drscca-officials.path` | Updates `officials.json` (never the Google Groups), then its readable copy in Drive |
| Email groups list | When an admin presses Refresh on the Email groups page | `drscca-groups.path` | `tools/groups_pull.py` reads every drscca.org group with GAM (read only) into `data/email-groups.json` |
| Software updates | Sundays 04:30 | `drscca-update.timer` | `deploy/gcp/update.sh`: newest Caddy, nginx and SFTP images; drapps rebuilt on the newest Python base image, tested inside the new image, started, and rolled back to the previous image if it does not start; newest GAM and Python libraries for the host jobs. The libraries inside the drapps image change only when the Python base image or `requirements.txt` changed, because Docker reuses that build step otherwise |
| System updates | daily | `unattended-upgrades` | Debian, Docker, Node and Google packages; reboots at 04:00 only when an update needs it |
| Secrets copy | Sundays 05:00 | `drscca-secrets-backup.timer` | Updates "drapps server secrets (weekly).tar.gz" in the admin-only Account Recovery folder (Drive keeps earlier versions). Fails, after saving, if anyone but admin@ can open the file |
| Website copy | Sundays 05:15 | `drscca-site-backup.timer` | drscca.org is on WordPress.com Premium (no do-it-yourself backups), so `deploy/gcp/site_backup.py` saves every published page, post, category, tag, the media list and the home page as JSON to "Website Archive/Weekly site backups" in the region drive; keeps 8 |

**Outside the server:** Google Cloud uptime checks look at drapps.drscca.org and livetiming.drscca.org every 5
minutes from three US locations, and email admin@ ("... is down") if two of them fail for 10 minutes (Cloud
console > Monitoring > Uptime checks and Alerting). These keep working even if the server is completely off.

**Protection:** fail2ban blocks an address for an hour after 5 wrong live timing passwords in 10 minutes
(`sudo fail2ban-client status livetiming-sftp`; to lift a ban, `sudo fail2ban-client set livetiming-sftp unbanip
<address>`). Container logs are capped at 3 x 10 MB each.

Check them: `systemctl list-timers 'drscca-*'` (six timers: qbo, backup, state-backup, update, secrets-backup,
site-backup) and `systemctl status 'drscca-*.path'` (three: officials, qbo-refresh, groups). Jobs that failed:
`systemctl --failed`.

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
export. Before saving, the forms warn when a new name would create a new person or join another person's results, and when a class is new for that year; a class that differs from an existing one only by case or spaces is refused. *Records > See every correction* lists each correction with before and after, and Undo: the app copies `records-base.db`, applies the remaining corrections and swaps it in. Undoing a removed event or result line that a rebuild already left out brings it back only at the next rebuild (the page says so). Never delete that file: it holds people's corrections. The nightly backup (`backup_sheets.py`) keeps 60 dated copies in `data/backups/`. New results that people add land in the Drive folder *Timing and Results / Results inbox*
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
| Caddy (HTTPS, certificates) | `sudo docker logs --since 1h caddy` |
| Live timing uploads and pages | `sudo journalctl CONTAINER_NAME=livetiming-sftp --since today` (the SFTP container logs to the journal), `sudo docker logs --since 1h livetiming-web` |
| A job's last run | `sudo journalctl -u drscca-qbo -n 50`. The jobs: `drscca-qbo` (nightly sync), `drscca-qbo-refresh` (Sync now), `drscca-backup` (sheets), `drscca-state-backup` (app files to Drive), `drscca-secrets-backup`, `drscca-site-backup`, `drscca-officials`, `drscca-groups` (Email groups Refresh), `drscca-update` (weekly update) |
| Alert emails that were sent or failed | `sudo journalctl -u 'drscca-alert@*' -n 30` |
| System updates | `/var/log/unattended-upgrades/unattended-upgrades.log` |
| Blocked live timing addresses | `sudo fail2ban-client status livetiming-sftp` |
| Run a job now | `sudo systemctl start drscca-backup.service` |
| Which version is running | `sudo docker inspect -f '{{index .Config.Labels "org.drscca.commit"}}' drapps`, and `sudo git -C /opt/drscca/app log -1 --oneline` for the code the host jobs use. They should match |
| Who did what | the Audit page in the app, or `tail app/data/audit.ndjson` |

## Backups

Everything the region keeps, and where its copies are. A backup job that ends with an error emails
admin@drscca.org.

| What | Copy | How often, how long | Where |
|---|---|---|---|
| The sheets (all region records) | Drive's own version history | Every edit | On each spreadsheet in the DRSCCA Apps drive |
| The sheets | `.xlsx` copy of every sheet (`backup_sheets.py`) | Nightly 02:45, 60 days | `data/backups/` on the server |
| The app's own files (audit log, Records corrections, short links, role settings, issued invoice numbers; see the `data/` table) | `drapps app state (nightly).tar.gz` (`secrets_backup.py --state`) | Nightly 03:00; Drive keeps earlier versions, about 30 days | Account Recovery folder, DRSCCA Region drive (admin only) |
| The whole server | Disk snapshot (schedule `daily-7`) | Daily, 7 days | Cloud console > Compute Engine > Snapshots |
| Server secrets and live timing logins | `drapps server secrets (weekly).tar.gz` (`secrets_backup.py`) | Sundays 05:00; Drive keeps earlier versions | Account Recovery folder, DRSCCA Region drive (admin only) |
| drscca.org website content | `drscca.org site backup YYYY-MM-DD.tar.gz` (`site_backup.py`) | Sundays 05:15, newest 8 | DRSCCA Region drive > Website Archive > Weekly site backups |
| drscca.org database, once | `drscca.org WordPress.com database 2026-11-09 (before Premium downgrade).sql.gz` | One time, the last full export | Account Recovery folder (holds user emails and password hashes) |
| The old website (IONOS, to 2026) | Full archive | One time | DRSCCA Region drive > Website Archive |
| The app's code | This repository | Every change | GitHub, detroit-region-scca/drapps |

**The two copies in the Account Recovery folder** (secrets, app state) hold keys, names, email addresses and
working links, so they go only to that folder. Each run removes the "anyone at drscca.org" permission Workspace
adds to new files, then reads back who can open the file. If that is anyone but admin@, the copy is still saved
and the job fails, so an email goes out naming who else can open it. Either fix the folder's sharing, or, if
those people are meant to have access, list them in `BACKUP_READERS` in `drapps.env` (comma separated, admin@
included). The folder and the region drive are `ACCOUNT_RECOVERY_FOLDER_ID` and `REGION_DRIVE_ID` in
`drapps.env`; without them the backup jobs stop with a message saying which one is missing (and the alert email goes out).

**Restoring the app's own files.** Download `drapps app state (nightly).tar.gz` (for an older night: right
click > File information > Manage versions), unpack it, and copy the files you need into
`/opt/drscca/app/data/` with `sudo install -o 1000 -g 1000 -m 644 <file> /opt/drscca/app/data/`. Then
`sudo docker compose restart drapps`.

**The website copy.** drscca.org is on WordPress.com Premium, which has no do-it-yourself backups or restores
(Business has them). Every Sunday the server reads the site's public WordPress API and saves `pages.json`,
`posts.json`, `categories.json`, `tags.json`, `media.json` (every public file with its address), `home.html` and a
README into one `.tar.gz`. It reads only published, public content, so it needs no WordPress login; drafts, the theme's
templates and site settings are not in it. If a run gets back fewer than 10 pages or posts it stops with an error and
keeps the older copies. For a fuller copy at any time: wp-admin > Tools > Export (posts, pages and media as a WordPress
export file), saved by hand into the same folder.

**Restoring a page or post.** Open the newest copy, find it in `pages.json` or `posts.json` by its `slug`, and paste
its `content.rendered` into a Custom HTML block in the editor (or rebuild it from that text). Each item also has its
title, date, categories and featured image id; `media.json` gives each image's address.

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
group for their job (the Email groups page; members are changed at admin.google.com), and their spam folder.

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
`data/officials-done.json` show what happened to each change. A change the job refused (for example it would
leave a needed role empty) is recorded there with its reason and shown in the app: make the change again,
corrected. If the
job did not run at all, check `systemctl status drscca-officials.path`, then save the person again in
Admin > Users. If the log says "Drive copy failed", `officials.json` was changed and only its readable copy in
Drive is behind: the next save or the nightly sync writes it again.

**A deploy failed.** Read the banner.
`DEPLOY FAILED` before the start: nothing changed on the site; fix the test or build error and deploy again.
`DEPLOY FAILED AND WAS ROLLED BACK`: the site is on the previous version; the failed start's log lines are above
the banner. `DEPLOY NOT FINISHED`: the app is on the new version but a later setup step failed; the banner
names it. `THE ROLLBACK FAILED TOO`: the site may be down. On the server, in `/opt/drscca`:
`sudo docker compose ps`, `sudo docker logs --tail 80 drapps`, `sudo docker images drapps` (you should see
`latest`, `previous` and `candidate`). To start the previous version by hand:
`sudo docker tag drapps:previous drapps:latest && sudo docker compose up -d --no-build drapps`. If the compose
file is the problem, the one from before the deploy is `docker-compose.yml.previous`; the Caddyfile from before
is `Caddyfile.previous`.

**The weekly update email says it failed.** The log lines in the email say which case. Tests failed or the image
did not build: the old image is still running and nothing needs doing until the cause is fixed in the code or
in `requirements.txt` (usually a new library version; pin it, deploy). Rolled back: the same, and the site was
down for about a minute and a half. Rollback failed: treat as "A deploy failed" above.

**A sheet was damaged or rows were deleted.** First use Drive's version history on that spreadsheet. If that is
not enough, the matching file in `data/backups/` is the sheet as of a past night: copy back what is missing. Do
not replace the whole spreadsheet, or its ID changes.

**A page or post on drscca.org was deleted or damaged.** First look in WordPress: Pages or Posts > Trash (kept 30
days), or the page's Revisions in the editor. If it is not there, restore it from the weekly website copy (see
Backups above).

**The server itself is lost or broken.** The disk is snapshotted daily, 7 days kept (snapshot schedule
`daily-7`): in the Cloud console, create a disk from the latest snapshot and a VM from that disk with the same
network tag `drapps` and the IP `drapps-ip`. This is the quick way, and it brings everything back as of that
night. Then do steps 10 and 11 below.

**To build it from scratch instead.** Steps marked *check on the server* could not be confirmed from this repo:
compare with the running server, or a disk made from a snapshot, before you rely on them.

1. **The VM.** Debian 12, e2-micro, zone `us-central1-a`, project `drscca-automation`, network tag `drapps`,
   fixed IP `drapps-ip`. Attach the snapshot schedule `daily-7` to its disk (Compute Engine > Snapshots >
   Snapshot schedules).
2. **Firewall.** The project's firewall rules apply to the tag `drapps`, so a new VM with that tag gets them:
   web (80, 443) and live timing (2222) from anywhere, SSH (22) only from Google's IAP range
   (35.235.240.0/20). The rule names are *check on the server* (Cloud console > VPC network > Firewall). You
   need the *IAP-secured Tunnel User* role to connect.
3. **Packages.** Docker with the compose plugin (Docker's own apt repository), `git`, `python3-venv`, Node.js
   (the NodeSource repository; the QuickBooks tools need it), `unattended-upgrades`. Exact versions are *check
   on the server*. The deploy installs fail2ban, the apt settings and the time zone itself.
4. **The host user.** A user `drapps` with uid and gid 1000 (the container runs as uid 1000 and they share
   `app/data/`; the QuickBooks jobs run as this user). For example:
   `sudo useradd -u 1000 -U -d /opt/drscca -s /usr/sbin/nologin drapps`. How the old one was made, and what to
   do if Debian already gave uid 1000 to your login user, is *check on the server*.
5. **The code.** `sudo mkdir -p /opt/drscca && sudo git clone https://github.com/detroit-region-scca/drapps
   /opt/drscca/app` (root owns the clone; the deploy runs git with sudo). Then
   `sudo install -d -o 1000 -g 1000 /opt/drscca/app/data`.
6. **Secrets.** Download `drapps server secrets (weekly).tar.gz` from the Account Recovery folder and unpack it
   at `/` as root: it restores `/opt/drscca/data/secrets/` (settings, keys, `officials.json`, the GAM config in
   `gam/`), and the live timing logins and host keys in `/opt/drscca/data/livetiming/`. Unpacked as root with
   `tar -xpzf`, every file keeps the owner and permissions it had. `secrets/qbo/` must belong to drapps (the
   deploy sets that).
   `drapps.env` holds the settings: `.env.example` in this repo lists every key and what it is for.
7. **Live timing folders.** `sudo mkdir -p /opt/drscca/data/livetiming/www/autocross
   /opt/drscca/data/livetiming/www/rallycross`, each owned by the uid on its line in `users.conf`. They start
   empty; the next upload fills them.
8. **The app's own files.** Download `drapps app state (nightly).tar.gz` from the same folder and unpack it at
   `/` as root, then `sudo chown -R 1000:1000 /opt/drscca/app/data`. Copy in `records.db` (see "Records").
   The nightly `.xlsx` copies in `backups/` are not in it; they start again from tonight.
9. **QuickBooks tools.** `/opt/drscca/qbo-mcp` (a Node program; the sync and `qbo-mcp.sh` run
   `/opt/drscca/qbo-mcp/dist/index.js`). It is not in this repo and not in the secrets copy: where it was
   installed from is *check on the server*. A disk made from a snapshot has it.
10. **Deploy.** From your computer: `deploy/gcp/deploy.sh`. It builds and starts the app, starts Caddy and live
    timing, creates `/opt/drscca/venv` with the Python libraries, and installs and turns on every job. Then
    install GAM for the Email groups page: `sudo /opt/drscca/venv/bin/pip install gam7` (the weekly update
    keeps it current after that).
11. **Check.** `sudo docker compose ps` shows four containers up. `systemctl list-timers 'drscca-*'` shows six
    timers and `systemctl status 'drscca-*.path'` three paths. `sudo systemctl start drscca-alert@test.service`
    delivers an email. If DNS had to change (a new IP), update the three A records at WordPress.com; Caddy gets
    new certificates by itself once they point here.
12. **QuickBooks.** The restored QuickBooks token will not work: QuickBooks replaces the token every time it is
    used, so any copy is out of date by the next night. Reconnect as a QuickBooks admin and put the new refresh
    token in `qbo/qbo.env` (see "QuickBooks sync fails"), then `sudo systemctl start drscca-qbo.service`.
13. **Outside the server.** The uptime checks and the budget alert belong to the Google Cloud project, not the
    VM, so they survive. If the address or host names changed, update them (Cloud console > Monitoring >
    Uptime checks). The OAuth client for Google sign-in and the service accounts also belong to the project.

## Secrets

All on the server in `/opt/drscca/data/secrets/` (root only), never in git. A copy of anything you cannot
recreate belongs in the admin-only Account Recovery folder in the region shared drive.

| Secret | File | How to replace |
|---|---|---|
| App settings incl. `FLASK_SECRET_KEY`, `API_KEYS` | `drapps.env` | Edit, restart drapps. A new `FLASK_SECRET_KEY` signs everyone out and voids emailed links. |
| App service account key | `drapps-sa.json` | New key in Google Cloud, swap the file, restart, delete the old key |
| Workspace service account key (GAM, the copies to Drive) | `workspace-sa.json`, `gam/gam.cfg` | New key, swap the file |
| QuickBooks token | `qbo/qbo.env` | Reconnect as above |
| Live timing logins | `/opt/drscca/data/livetiming/users.conf` | See "Live timing" |
| Officials list | `officials.json` | Not secret, but private: names and addresses |

## Handing over

The new maintainer needs the four things in "What you need", and this page. Walk through one deploy and one
restart together. When a maintainer leaves: remove their access to the Google Cloud project, the repo and the
drive, and replace `FLASK_SECRET_KEY`, the service account keys and the API key they used.
