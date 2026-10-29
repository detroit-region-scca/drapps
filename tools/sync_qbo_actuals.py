"""Copy each event project's QuickBooks profit and loss into the Event Budgets sheet ('QBO Actuals' tab).

Run on the machine with the QuickBooks connection (see qbo_actuals.mjs); the web app only reads the tab.
Needs node, the QBO env (QUICKBOOKS_* from the qbo-mcp token store) and the app's .env for the sheet.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(HERE), '.env'))
os.environ['GOOGLE_SERVICE_ACCOUNT_FILE'] = os.environ.get('GOOGLE_SA_KEY_HOST') or os.environ['GOOGLE_SERVICE_ACCOUNT_FILE']
import bud_store as B  # noqa: E402

COLS = ['event_code', 'project', 'section', 'account', 'amount', 'as_of']

data = json.loads(subprocess.run(['node', os.path.join(HERE, 'qbo_actuals.mjs')], check=True, capture_output=True, text=True).stdout)
rows = [[p['code'], p['project'], ln['section'], ln['account'], ln['amount'], data['as_of']] for p in data['projects'] for ln in p['lines']]
book = B.open_book(os.environ['BUDGETS_SHEET_ID'])
try:
    ws = book.worksheet('QBO Actuals')
except Exception:
    ws = book.add_worksheet('QBO Actuals', rows=len(rows) + 100, cols=len(COLS))
if len(rows) + 1 > ws.row_count:
    ws.add_rows(len(rows) + 1 - ws.row_count + 100)
# Write first, then clear the leftover rows below, so the tab is never empty mid-sync.
ws.update([COLS] + rows, 'A1', value_input_option='RAW')
if ws.row_count > len(rows) + 1:
    ws.batch_clear([f'A{len(rows) + 2}:F{ws.row_count}'])
print(f"QBO actuals as of {data['as_of']}: {len(data['projects'])} projects, {len(rows)} account lines")
