# DRSCCA Apps

The Detroit Region SCCA's web apps for directors, officers, the treasurer and volunteers, in one Flask app at
drapps.drscca.org. Keeping it running: [docs/RUNBOOK.md](docs/RUNBOOK.md).

| App | Path | What it does |
|---|---|---|
| Events | `/events` | Register an event (it gets the `#DR` code used in MotorsportReg and QuickBooks), build its budget, see budget vs QuickBooks, enter reported results |
| Members | `/members` | Worker points: enter who worked an event, milestones, using points, the books entry for the treasurer |
| Board | `/board` | Budgets and motions waiting for a vote, email votes (each director gets their own Yes / No / Abstain link; the roll call shows who has voted), recorded decisions, minutes |
| Money | `/funds`, `/funds/in` | Money out: ask for a check, a region card payment or a reimbursement, approved by emailed link. Money in: report cash and checks received; marked deposited when QuickBooks shows the deposit |
| Annual Plan | `/plan` | Each program's yearly plan against QuickBooks |
| Operations | `/operations` | Vehicles, trailers, storage, recurring bills, documents, to-dos, replacement plan |
| Invoices | `/invoices` | Invoices the region sends, as PDFs, emailed to the customer |
| Volunteers | `/access` | Add and remove volunteers who can sign in |
| Treasurer | `/treasurer` | Payments, approvals, the books check, month end, QuickBooks sync |
| Admin | `/admin` | Users and roles, role permissions, programs |

## How it fits together

- **Data lives in Google Sheets** in the region's **DRSCCA Apps** Shared Drive, one spreadsheet per app, with
  receipts, budget files, documents, minutes and invoice PDFs in Drive folders beside them. The app reads and
  writes them as a Google service account. Anyone with Drive access can read the data without the app.
- **The server keeps a little state of its own** in `data/`: the audit log, sign-in codes, programs, role
  permissions, the officials change queue, the QuickBooks sync status, a cache of the sheets, and nightly
  backups. The runbook lists each file.
- **Who holds which job** is `officials.json` on the host, mounted read-only. The admin changes it in
  Admin > Users; a host job (`tools/officials_queue.py`) applies the change and updates the drscca.org Google
  Groups to match. Nobody can make themselves an approver from inside the app.
- **What each role may do** is set in Admin > Roles (`data/role-access.json`). The home page grays out apps a
  role does not use. Volunteers have their own role there, by default events, entering worker points, and their
  own funds requests and invoices.
- **Sign-in** is by a code emailed to the person, or Google sign-in when it is configured. No shared password.
- **Approvals happen by email.** Anything that commits money (director, second signer, board) is approved
  through a signed link. App email goes to role groups (autocross@, treasurer@ and so on), never a person's
  own inbox, so mail follows the job. The sign-in code is the one exception.
- **QuickBooks is read only and runs outside the web app.** A nightly host job reads QuickBooks and writes
  actuals, paid status and a books check into the sheets. The Treasurer page's Sync now button asks for an
  extra run.

## Setting up a new host

1. **A server with Docker** and HTTPS in front (Caddy, nginx or a Cloudflare Tunnel). The container listens
   on `HOST_PORT` (default 5858).
2. **The code:** `git clone` this repo.
3. **A Google service account** with a JSON key, added as *Content manager* of the DRSCCA Apps Shared Drive.
   Its Google Cloud project needs the Sheets and Drive APIs. The data stays where it is.
4. **`.env`:** copy `.env.example` and fill it in. Every setting has a comment. The sheet and folder IDs are
   the long IDs in each Google URL.
5. **`officials.json`:** copy the current one, or start from `officials.example.json`.
6. **Email:** either SMTP (a Workspace address with an app password works) or the Gmail API as the service
   account (`MAIL_TRANSPORT=gmail_api`). Send from a drscca.org address so approvals stay out of spam.
7. **Copy `data/`** from the old host (see the runbook for what is in it).
8. **Start it:** `docker compose -f docker-compose.prod.yml up -d --build`
9. **Host jobs:** install the systemd user units in `deploy/` (runbook, "Automatic jobs"). Fix the paths in
   `drscca-qbo.service` first.
10. **Point DNS** at the new host, sign in, open each app, and file a test funds request to confirm email.

## Configuration

All settings are in `.env`; `.env.example` lists each one. The secrets are `FLASK_SECRET_KEY`, the SMTP
password if used, the Google OAuth client secret, `API_KEYS`, the service account key file and
`officials.json`. None belong in git. **This repo is public:** never commit sheet IDs, keys, member names,
numbers or emails, including in tests.

Programs (the codes in event numbers and funds requests: AX, RR, RY, RX, SS, SIM, DR, OPS) are in
`data/programs.json` and are edited in Admin > Programs.

## Development

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt pytest
OFFICIALS_HOST=/nonexistent PYTHONPATH=. venv/bin/pytest -q tests
venv/bin/python app.py            # http://localhost:5858, reads .env
```

Tests run on GitHub for every push (`.github/workflows/test.yml`). `docker-compose.debug.yml` runs the Flask
dev server with the code mounted.

## Code map

- `app.py`: the home page, event registration, the Treasurer page, the audit page; registers the apps.
- One app per pair of files, plus rules kept apart so they can be tested: `bud.py`/`bud_store.py` (events and
  budgets), `members.py`/`members_store.py`/`members_logic.py`, `board.py`/`motions_store.py`,
  `minutes.py`/`minutes_store.py`/`minutes_logic.py`, `fr.py`/`fr_store.py`/`fr_logic.py` (money out) with `received_store.py` (money in),
  `plan.py`/`plan_store.py`, `equip.py`/`equip_store.py` (with `equip_docs.py`, `equip_pdf.py`),
  `inv.py`/`inv_store.py`, `admin.py`. The `*_store.py` files are the only code that touches the sheets.
- `storage.py`: Google Sheets access shared by every app, and the event registry. `sheet_cache.py`: the cache.
- `auth.py` (sign-in, volunteers), `access.py` (roles and permissions), `officials.py`, `officer.py`,
  `csrf.py`.
- `utils.py`: email, dates, the audit log, shared helpers. `qbo_logic.py`: matching QuickBooks to the sheets.
- `templates/`: one Jinja template per app. `tools/`: host jobs. `deploy/`: their systemd units.

## API and MCP server

`/api/v1` (`api.py`) is a JSON API. Send `Authorization: Bearer <key>`; keys are `API_KEYS` in `.env`
(`email:key` pairs; the email must be an official, and the key acts as that official, with full read and write
access). Writes go through the app's own form pages in the same process, so every rule, email and audit entry
matches the website.

`mcp_server/drapps_mcp.py` wraps the API for Claude and other MCP clients: events, budgets, plans, actuals, funds
requests with receipt files, invoices, Operations to-dos and property, members and worker points, the Board
(waiting items, vote requests, decisions, motions), volunteers, and the audit log. Install and register:

    python3 -m venv ~/.mcp-servers/drevent-mcp/venv
    ~/.mcp-servers/drevent-mcp/venv/bin/pip install -r mcp_server/requirements.txt
    claude mcp add drapps -s user -- ~/.mcp-servers/drevent-mcp/venv/bin/python -I \
        ~/projects/event_drscca_app/mcp_server/drapps_mcp.py

It reads `DRAPPS_URL` and `DRAPPS_API_KEY` from `~/.secrets/drevent-api.env` (or the file in `DRAPPS_ENV`); the
older `DREVENT_*` names still work.

## Officials and the drscca.org groups

`officials.json` is the one list of who holds which job. Admin > Users is the normal way to change it. On the
host, `tools/handover.py` does the same from the command line, using GAM as the region's service account.
Nothing changes without `--apply`.

    python3 tools/handover.py show                      # who each group should reach
    python3 tools/handover.py sync [--apply]            # fix groups that drifted from the file
    python3 tools/handover.py hand "Autocross Director" new@example.com "New Person" [--from old@example.com] [--apply]

The app picks up the edited file on its next read, with no restart.

Each official has `email` (the person: sign-in matches it, and group membership uses it) and `notify` (the
role's group, for example autocross@drscca.org). The app emails only `notify` addresses, so mail lands in the
group's shared inbox. A handover changes `email`; `notify` stays with the role. Volunteers get mail at their own
address. Region accounts (admin@, treasurer@) are Google Workspace accounts or groups, handed over in
Workspace, not by this tool.
