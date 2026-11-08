// The only QuickBooks write the apps make, asked for by qbo_nightly.py as one JSON list on stdin:
//   {op: 'attach', ref, type, id, files: [{path, name, contentType}]}
//       uploads receipts onto a QuickBooks transaction (type Purchase or BillPayment). A file whose name is
//       already attached to that transaction is skipped, so running it twice adds nothing. Amounts never change.
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

const jobs = JSON.parse(readFileSync(0, 'utf8'));
const out = [];
for (const job of jobs) {
  try {
    out.push({ ref: job.ref, ok: true, ...(await attach(job)) });
  } catch (e) {
    out.push({ ref: job.ref, ok: false, error: String(e.message || e) });
  }
}
console.log(JSON.stringify(out));
