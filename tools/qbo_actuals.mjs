// Prints every event project's profit and loss by account as JSON, for sync_qbo_actuals.py.
// Runs on the machine that holds the QuickBooks connection (the qbo-mcp install and its token
// store); the web app never talks to QuickBooks. QBO_MCP_DIR points at that install.
const dir = process.env.QBO_MCP_DIR || `${process.env.HOME}/.mcp-servers/qbo-mcp`;
const { QuickbooksClient } = await import(`${dir}/dist/clients/quickbooks-client.js`);
const c = await QuickbooksClient.getAuthCredentials();
const base = `https://quickbooks.api.intuit.com/v3/company/${c.realmId}`;
const get = async (path) => {
  const r = await fetch(`${base}/${path}${path.includes('?') ? '&' : '?'}minorversion=75`,
    { headers: { Authorization: `Bearer ${c.accessToken}`, Accept: 'application/json' } });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
};
let customers = [], start = 1;
while (true) {
  const q = encodeURIComponent(`SELECT * FROM Customer STARTPOSITION ${start} MAXRESULTS 1000`);
  const page = (await get(`query?query=${q}`)).QueryResponse?.Customer || [];
  customers = customers.concat(page);
  if (page.length < 1000) break;
  start += 1000;
}
const today = new Date().toISOString().slice(0, 10);
const projects = [];
for (const cu of customers.filter(x => x.IsProject && /#DR\w+/.test(x.DisplayName))) {
  const code = cu.DisplayName.match(/#DR\w+/)[0];
  const rep = await get(`reports/ProfitAndLoss?customer=${cu.Id}&start_date=2025-01-01&end_date=2027-12-31&accounting_method=Accrual`);
  const lines = [];
  // Leaf account rows only; the section comes from the top-level group (Income vs COGS/Expenses).
  const walk = (rows, section) => {
    for (const r of rows || []) {
      const top = r.group || section;
      if (r.ColData && r.type !== 'Section') {
        const amount = parseFloat(r.ColData[1]?.value || '0');
        if (amount) lines.push({ section: /Income/i.test(top) ? 'Income' : 'Expense', account: r.ColData[0].value, amount });
      }
      if (r.Rows) walk(r.Rows.Row, top);
    }
  };
  walk(rep.Rows?.Row, '');
  projects.push({ code, project: cu.DisplayName, lines });
}
console.log(JSON.stringify({ as_of: today, projects }));
