// Prints what the apps need from QuickBooks as one JSON document, for qbo_nightly.py:
//   projects  each #DR event project's profit and loss by account
//   programs  the region's profit and loss by account, per calendar year
//   monthly   the same by account and month, this year and last, for the plan vs actual chart
//   balances  bank accounts and the worker points reserve
//   payments  checks, bill payments and card charges since PAYMENTS_SINCE
//   outstanding  checks since PAYMENTS_SINCE the bank has not cleared yet (QuickBooks "Clr" blank; voided $0 checks left out)
//   deposits  deposit lines since PAYMENTS_SINCE (date, amount, description, payer), to match against open invoices
// Runs on the machine that holds the QuickBooks connection (the qbo-mcp install and its token
// store); the web app never talks to QuickBooks. QBO_MCP_DIR points at that install. Read only.
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
const query = async (entity, where = '') => {
  let out = [], start = 1;
  while (true) {
    const q = encodeURIComponent(`SELECT * FROM ${entity} ${where} STARTPOSITION ${start} MAXRESULTS 1000`);
    const page = (await get(`query?query=${q}`)).QueryResponse?.[entity] || [];
    out = out.concat(page);
    if (page.length < 1000) return out;
    start += 1000;
  }
};
// Leaf account rows of a ProfitAndLoss report; the section comes from the top-level group.
const pnlLines = (rep) => {
  const lines = [];
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
  return lines;
};

const today = new Date().toISOString().slice(0, 10);
const year = Number(today.slice(0, 4));
const since = process.env.PAYMENTS_SINCE || `${year - 1}-10-01`;

const projects = [];
for (const cu of (await query('Customer')).filter(x => x.IsProject && /#DR\w+/.test(x.DisplayName))) {
  const rep = await get(`reports/ProfitAndLoss?customer=${cu.Id}&start_date=2025-01-01&end_date=${year + 1}-12-31&accounting_method=Accrual`);
  projects.push({ code: cu.DisplayName.match(/#DR\w+/)[0], project: cu.DisplayName, active: cu.Active, lines: pnlLines(rep) });
}

const programs = [];
for (const y of [year - 1, year, year + 1]) {
  const rep = await get(`reports/ProfitAndLoss?start_date=${y}-01-01&end_date=${y}-12-31&accounting_method=Accrual`);
  programs.push({ year: y, lines: pnlLines(rep) });
}

// Leaf account rows of a ProfitAndLoss report summarized by month: one amount per month column (Total dropped).
const monthLines = (rep) => {
  const lines = [];
  const walk = (rows, section) => {
    for (const r of rows || []) {
      const top = r.group || section;
      if (r.ColData && r.type !== 'Section') {
        const months = r.ColData.slice(1, 13).map(d => parseFloat(d.value || '0') || 0);
        if (months.some(Boolean)) lines.push({ section: /Income/i.test(top) ? 'Income' : 'Expense', account: r.ColData[0].value, months });
      }
      if (r.Rows) walk(r.Rows.Row, top);
    }
  };
  walk(rep.Rows?.Row, '');
  return lines;
};
const monthly = [];
for (const y of [year - 1, year]) {
  const rep = await get(`reports/ProfitAndLoss?start_date=${y}-01-01&end_date=${y}-12-31&accounting_method=Accrual&summarize_column_by=Month`);
  monthly.push({ year: y, lines: monthLines(rep) });
}

const balances = (await query('Account', "WHERE AccountType IN ('Bank', 'Other Current Liability')"))
  .filter(a => a.Active && (a.AccountType === 'Bank' || /Worker Awards/i.test(a.Name)))
  .map(a => ({ account: a.FullyQualifiedName, type: a.AccountType, balance: a.CurrentBalance }));

const payments = [];
for (const p of await query('Purchase', `WHERE TxnDate >= '${since}'`)) {
  payments.push({
    kind: p.PaymentType === 'Check' ? 'check' : p.PaymentType === 'CreditCard' ? 'card' : 'other',
    id: `Purchase ${p.Id}`, date: p.TxnDate, number: p.DocNumber || '', payee: p.EntityRef?.name || '',
    amount: p.TotalAmt, account: p.AccountRef?.name || '', memo: p.PrivateNote || '',
    lines: (p.Line || []).filter(l => l.Amount).map(l => ({ description: l.Description || '', amount: l.Amount })),
  });
}
for (const b of await query('BillPayment', `WHERE TxnDate >= '${since}'`)) {
  payments.push({
    kind: b.PayType === 'Check' ? 'check' : 'card', id: `BillPayment ${b.Id}`, date: b.TxnDate, number: b.DocNumber || '',
    payee: b.VendorRef?.name || '', amount: b.TotalAmt, account: (b.CheckPayment || b.CreditCardPayment)?.BankAccountRef?.name
      || b.CreditCardPayment?.CCAccountRef?.name || '', memo: b.PrivateNote || '', lines: [],
  });
}

const tl = await get(`reports/TransactionList?start_date=${since}&end_date=${today}&transaction_type=Check&columns=tx_date,txn_type,doc_num,name,is_cleared,subt_nat_amount`);
const tcols = (tl.Columns?.Column || []).map(c => c.ColTitle), tc = n => tcols.indexOf(n);
const trows = []; const twalk = rr => (rr || []).forEach(r => { if (r.ColData) trows.push(r.ColData.map(c => c.value)); if (r.Rows) twalk(r.Rows.Row); });
twalk(tl.Rows?.Row);
const outstanding = trows.filter(r => r[tc('Transaction Type')] === 'Check' && !r[tc('Clr')] && Math.abs(Number(r[tc('Amount')]) || 0) >= 0.005)
  .map(r => ({ date: r[tc('Date')], number: r[tc('Num')], payee: r[tc('Name')], amount: Math.abs(Number(r[tc('Amount')])) }));

const deposits = [];
for (const d of await query('Deposit', `WHERE TxnDate >= '${since}'`)) {
  for (const l of d.Line || []) {
    if (!l.Amount) continue;
    deposits.push({ id: `Deposit ${d.Id}`, date: d.TxnDate, amount: l.Amount, description: l.Description || '',
                    payer: l.DepositLineDetail?.Entity?.name || '' });
  }
}

console.log(JSON.stringify({ as_of: today, projects, programs, monthly, balances, payments, outstanding, deposits }));
