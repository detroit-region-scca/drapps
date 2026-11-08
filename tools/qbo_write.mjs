// The only QuickBooks writes the apps make, asked for by qbo_nightly.py as one JSON list on stdin:
//   {op: 'attach', ref, type, id, files: [{path, name, contentType}]}
//       uploads receipts onto a QuickBooks transaction (type Purchase or BillPayment). A file whose name is
//       already attached to that transaction is skipped, so running it twice adds nothing.
//   {op: 'invoice', ref, doc, date, due, customer: {name, email, address}, project, account, memo, note, lines: [{description, quantity, unit_price, amount}]}
//       creates the invoice once, numbered doc (the drapps invoice number). Billed to the event's QuickBooks
//       project when the invoice is for an event (so it lands in that event's profit and loss), otherwise to the
//       customer (found by name, or made). Each line uses a service item for the program's income account.
// Prints [{ref, ok, ...}] as JSON. Runs where the QuickBooks connection is (see qbo_export.mjs).
import { readFileSync } from 'node:fs';

const dir = process.env.QBO_MCP_DIR || `${process.env.HOME}/.mcp-servers/qbo-mcp`;
const { QuickbooksClient } = await import(`${dir}/dist/clients/quickbooks-client.js`);
const c = await QuickbooksClient.getAuthCredentials();
const base = `https://quickbooks.api.intuit.com/v3/company/${c.realmId}`;
const auth = { Authorization: `Bearer ${c.accessToken}`, Accept: 'application/json' };
const call = async (path, init = {}) => {
  const r = await fetch(`${base}/${path}${path.includes('?') ? '&' : '?'}minorversion=75`, { ...init, headers: { ...auth, ...(init.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok || body.Fault) throw new Error(`${path.split('?')[0]}: HTTP ${r.status} ${JSON.stringify(body.Fault?.Error?.[0] || body).slice(0, 300)}`);
  return body;
};
const esc = s => String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
const query = async (entity, where) => (await call(`query?query=${encodeURIComponent(`SELECT * FROM ${entity} ${where} MAXRESULTS 1000`)}`)).QueryResponse?.[entity] || [];
const post = (entity, obj) => call(entity.toLowerCase(), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(obj) });

async function attach(job) {
  const have = new Set((await query('Attachable', `WHERE AttachableRef.EntityRef.Type = '${job.type}' AND AttachableRef.EntityRef.value = '${esc(job.id)}'`))
    .map(a => a.FileName));
  const added = [];
  for (const f of job.files) {
    if (have.has(f.name)) continue;
    const form = new FormData();
    form.append('file_metadata_01', new Blob([JSON.stringify({
      AttachableRef: [{ EntityRef: { type: job.type, value: job.id } }], FileName: f.name, ContentType: f.contentType,
    })], { type: 'application/json' }), 'metadata.json');
    form.append('file_content_01', new Blob([readFileSync(f.path)], { type: f.contentType }), f.name);
    await call('upload', { method: 'POST', body: form });
    added.push(f.name);
  }
  return { added };
}

const items = {};
async function itemFor(accountName) {
  if (items[accountName]) return items[accountName];
  const acct = (await query('Account', `WHERE Name = '${esc(accountName)}' AND AccountType = 'Income'`))[0];
  if (!acct) throw new Error(`no income account named ${accountName}`);
  const name = `${accountName} (invoices)`.slice(0, 100);
  let it = (await query('Item', `WHERE Name = '${esc(name)}'`))[0];
  if (!it) it = (await post('Item', { Name: name, Type: 'Service', IncomeAccountRef: { value: acct.Id } })).Item;
  return (items[accountName] = it);
}

async function customerFor(job) {
  if (job.project) {
    const p = (await query('Customer', `WHERE DisplayName LIKE '%${esc(job.project)}%'`)).filter(x => x.IsProject && x.Active);
    if (p.length === 1) return { cust: p[0], project: true };
  }
  const name = job.customer.name.replace(/:/g, ' ').slice(0, 100);
  let cu = (await query('Customer', `WHERE DisplayName = '${esc(name)}'`))[0];
  if (!cu) {
    cu = (await post('Customer', {
      DisplayName: name, CompanyName: name.slice(0, 50),
      ...(job.customer.email ? { PrimaryEmailAddr: { Address: job.customer.email } } : {}),
    })).Customer;
  }
  return { cust: cu, project: false };
}

async function invoice(job) {
  const had = (await query('Invoice', `WHERE DocNumber = '${esc(job.doc)}'`))[0];
  if (had) return { id: had.Id, existed: true };
  const it = await itemFor(job.account);
  const { cust, project } = await customerFor(job);
  const addr = (job.customer.address || '').split('\n').map(s => s.trim()).filter(Boolean);
  const bill = [job.customer.name, ...addr].slice(0, 5);
  const inv = {
    DocNumber: job.doc, TxnDate: job.date, DueDate: job.due, CustomerRef: { value: cust.Id },
    BillAddr: Object.fromEntries(bill.map((s, i) => [`Line${i + 1}`, s.slice(0, 500)])),
    ...(job.customer.email ? { BillEmail: { Address: job.customer.email } } : {}),
    EmailStatus: 'NotSet', PrintStatus: 'NotSet',
    PrivateNote: job.note.slice(0, 4000), ...(job.memo ? { CustomerMemo: { value: job.memo.slice(0, 1000) } } : {}),
    Line: job.lines.map(l => ({
      DetailType: 'SalesItemLineDetail', Amount: l.amount, Description: l.description.slice(0, 4000),
      SalesItemLineDetail: { ItemRef: { value: it.Id }, Qty: l.quantity || 1, UnitPrice: l.unit_price },
    })),
  };
  const made = (await post('Invoice', inv)).Invoice;
  return { id: made.Id, total: made.TotalAmt, customer: cust.DisplayName, project };
}

const jobs = JSON.parse(readFileSync(0, 'utf8'));
const out = [];
for (const job of jobs) {
  try {
    out.push({ ref: job.ref, ok: true, ...(job.op === 'attach' ? await attach(job) : await invoice(job)) });
  } catch (e) {
    out.push({ ref: job.ref, ok: false, error: String(e.message || e) });
  }
}
console.log(JSON.stringify(out));
