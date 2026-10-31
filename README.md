# DRSCCA Apps

The Detroit Region SCCA's web apps for directors, officers and the treasurer, in one Flask app
(drevent.drscca.org):

| Section | Path | What it does |
|---|---|---|
| Event registry | `/` | Register events, assign the `#DR` code used in MotorsportReg and QuickBooks |
| Events and budgets | `/events` | Each event's budget, approval, and budget vs actual from QuickBooks |
| Annual plan | `/plan` | Each program's yearly plan and budget; officers record board approval |
| Funds requests | `/funds` | Reimbursement and payment requests, approved by emailed link |
| Worker points | `/wp` | The worker points ledger and redemption requests |
| Operations | `/operations` | Region property, storage, recurring bills, documents, to-dos, replacement plan |
| Invoices | `/invoices` | Invoices the region sends, as PDFs, emailed to the customer |
| Officer sign-in | `/officer` | RE, ARE and treasurer sign in by emailed link for officer-only actions |

## How it fits together

- **Data lives in Google Sheets** in the **DRSCCA Apps** Shared Drive (one spreadsheet per section),
  with uploaded receipts, budget files, documents and invoice PDFs in Drive folders beside them. The app
  reads and writes them as a Google service account. The server holds no data of its own except the audit
  log and backups (see below).
- **Who approves what** comes from `officials.json`, a file on the server mounted read-only into the
  container. Nobody can make themselves an approver through the app. Format: `officials.example.json`.
- **Approvals happen by email.** The site uses one shared password, so anything that commits money
  (director, second signer, board) is approved through a signed link emailed to the official, and
  officer-only actions need an emailed sign-in link. Email goes out over SMTP.
- **QuickBooks is optional and one-way.** A nightly job reads QuickBooks and writes actuals, paid status
  and a books check into the sheets. The web app never talks to QuickBooks.

## Moving the app to a new host

What a new host needs, in order:

1. **A server with Docker** and a domain or subdomain with HTTPS in front (Caddy, nginx, or a
   Cloudflare Tunnel). The container listens on `HOST_PORT` (default 5858).
2. **The code:** `git clone` this repo.
3. **A Google service account** with a JSON key, added as a *Content manager* of the DRSCCA Apps Shared
   Drive (Shared Drive > Manage members). The Google Sheets and Drive APIs must be enabled in its Google
   Cloud project. The existing data stays where it is; the new host just gets access.
4. **`.env`:** copy `.env.example` and fill it in. The sheet and folder IDs are the long IDs in each
   Google URL; copy them from the current host's `.env` or from Drive.
5. **`officials.json`:** copy the current one, or start from `officials.example.json`.
6. **Email:** any SMTP account that can send as the `SMTP_FROM` address, for example a Google Workspace
   address with an app password (`smtp.gmail.com`, port 587). Sending from a drscca.org address keeps the
   approval emails out of spam.
7. **Copy `data/`** from the old host. It holds `audit.ndjson` (the app's audit log, which is not in
   the sheets), `programs.json`, and `backups/`.
8. **Start it:** `docker compose -f docker-compose.prod.yml up -d --build`
9. **Nightly jobs:** install the timers in `deploy/` (see below).
10. **Point DNS** at the new host, sign in, and check each section loads. Send yourself a test funds
    request to confirm email works.

## Configuration

All settings are in `.env`; `.env.example` lists every one with a comment. The secrets are
`FLASK_SECRET_KEY`, `APP_PASSWORD`, `SMTP_PASSWORD`, the service account key file and `officials.json`.
None of them belong in git. This repo is public, so never commit sheet IDs, keys, member names or emails.

Programs (the codes in event numbers and funds requests) are in `data/programs.json`.

## Nightly jobs (`deploy/`)

- **Sheet backups**, `deploy/drscca-backup.*`: an `.xlsx` copy of every spreadsheet into `data/backups/`,
  kept for 60 days. A second copy in case Drive files are damaged or deleted.
- **QuickBooks sync**, `deploy/drscca-qbo.*`, optional: `tools/qbo_nightly.py` runs `tools/qbo_export.mjs`,
  which reads QuickBooks through a [qbo-mcp](https://github.com/intuit/quickbooks-online-mcp-server)
  install and its token store (`QBO_MCP_DIR`). It needs Node, an Intuit developer app connected to the
  region's QuickBooks company, and the app's `.env`. It:
  - copies each `#DR` event project's profit and loss to the Event Budgets sheet,
  - copies the region profit and loss by program and year, bank balances and the worker points reserve to
    the Annual Plan sheet,
  - marks open funds requests paid when QuickBooks has a matching check or card line,
  - lists exceptions for the treasurer on Annual Plan > Books check.

  Run by hand: `set -a && . /path/to/qbo.env && set +a && venv/bin/python tools/qbo_nightly.py --dry-run`

  Without it the apps still work; budget vs actual and paid status just stop updating.

## Development

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt pytest
PYTHONPATH=. venv/bin/pytest -q tests
venv/bin/python app.py            # http://localhost:5858, reads .env
```

Tests also run on GitHub for every push (`.github/workflows/test.yml`).
`docker-compose.debug.yml` runs the Flask dev server with the code mounted.

## Code map

- `app.py`: the event registry, sign-in, and the home page; registers the other sections.
- One section per pair of files: `bud.py`/`bud_store.py` (events and budgets), `plan.py`/`plan_store.py`,
  `fr.py`/`fr_store.py` (funds requests, rules in `fr_logic.py`), `wp.py`/`wp_store.py` (rules in
  `wp_logic.py`), `equip.py`/`equip_store.py` (Operations, with `equip_docs.py` and `equip_pdf.py`),
  `inv.py`/`inv_store.py`. The `*_store.py` files are the only code that touches the sheets.
- `storage.py`: Google Sheets access shared by all sections, and the event registry store.
- `officials.py`, `officer.py`: who the officials are, and officer sign-in.
- `utils.py`: email, dates, shared helpers. `templates/`: one Jinja template per section.
