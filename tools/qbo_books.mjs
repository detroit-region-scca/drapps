// A full, read-only copy of the QuickBooks books for the off-site backup: every list and transaction type, all
// years, one JSON file per type, plus the company settings and this year's trial balance. Run by qbo_nightly.py
// (same connection and token store as the sync) into QBO_BOOKS_DIR; the nightly off-site copy
// (deploy/gcp/offsite_backup.py) sends that folder to Cloud Storage, keeping a dated copy of each file that changed.
// Receipts attached in QuickBooks are not downloaded: the app keeps them in the Receipts folder in Drive.
// Prints one JSON line: {"ok": [...types], "failed": {type: error}, "dir": ...}.
import { mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const out = process.env.QBO_BOOKS_DIR;
if (!out) throw new Error('QBO_BOOKS_DIR is not set');
const dir = process.env.QBO_MCP_DIR || `${process.env.HOME}/.mcp-servers/qbo-mcp`;
const { QuickbooksClient } = await import(`${dir}/dist/clients/quickbooks-client.js`);
const c = await QuickbooksClient.getAuthCredentials();
const base = `https://quickbooks.api.intuit.com/v3/company/${c.realmId}`;
const get = async (path) => {
  const r = await fetch(`${base}/${path}${path.includes('?') ? '&' : '?'}minorversion=75`,
    { headers: { Authorization: `Bearer ${c.accessToken}`, Accept: 'application/json' } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
};
const query = async (entity) => {
  let rows = [], start = 1;
  while (true) {
    const q = encodeURIComponent(`SELECT * FROM ${entity} STARTPOSITION ${start} MAXRESULTS 1000`);
    const page = (await get(`query?query=${q}`)).QueryResponse?.[entity] || [];
    rows = rows.concat(page);
    if (page.length < 1000) return rows;
    start += 1000;
  }
};
const save = (name, data) => {
  const f = join(out, `${name}.json`);
  writeFileSync(`${f}.tmp`, JSON.stringify(data, null, 1));
  renameSync(`${f}.tmp`, f);
};

// Every type the QuickBooks query API lists; ones this company does not use come back empty.
const TYPES = ['Account', 'Attachable', 'Bill', 'BillPayment', 'Budget', 'Class', 'CreditMemo', 'Customer', 'Department',
  'Deposit', 'Employee', 'Estimate', 'Invoice', 'Item', 'JournalEntry', 'Payment', 'PaymentMethod', 'Purchase',
  'PurchaseOrder', 'RefundReceipt', 'SalesReceipt', 'TaxCode', 'TaxRate', 'Term', 'TimeActivity', 'Transfer', 'Vendor',
  'VendorCredit'];
// The books themselves: if any of these cannot be read, the copy is not good enough and the job must fail.
const MUST = ['Account', 'Bill', 'BillPayment', 'Customer', 'Deposit', 'Invoice', 'JournalEntry', 'Payment', 'Purchase',
  'SalesReceipt', 'Transfer', 'Vendor'];

mkdirSync(out, { recursive: true });
const ok = [], failed = {};
for (const t of TYPES) {
  try {
    save(t, await query(t));
    ok.push(t);
  } catch (e) {
    failed[t] = String(e.message || e);
  }
}
for (const [name, path] of [['CompanyInfo', `companyinfo/${c.realmId}`], ['Preferences', 'preferences'],
  ['TrialBalance', `reports/TrialBalance?start_date=${new Date().getFullYear()}-01-01&end_date=${new Date().toISOString().slice(0, 10)}`]]) {
  try {
    save(name, await get(path));
    ok.push(name);
  } catch (e) {
    failed[name] = String(e.message || e);
  }
}
const missing = MUST.filter(t => failed[t]);
console.log(JSON.stringify({ ok, failed, missing, dir: out }));
