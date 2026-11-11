// Prints what the apps need from QuickBooks as one JSON document, for qbo_nightly.py:
//   projects  each #DR event project's profit and loss by account
//   programs  the region's profit and loss by account, per calendar year
//   monthly   the same by account and month, this year and last, for the plan vs actual chart
//   balances  bank accounts and the worker points reserve
//   payments  checks, bill payments and card charges since PAYMENTS_SINCE
//   outstanding  checks since PAYMENTS_SINCE the bank has not cleared yet (QuickBooks "Clr" blank; voided $0 checks left out)
//   deposits  deposit lines since PAYMENTS_SINCE (date, amount, description, payer), to match against open invoices
//   balance_sheet  today's balance sheet to two levels; receivables  every invoice still owed
//   pnl       the profit and loss statement from the first of last month through today, and the year to date, every line, with each line's
//             average for the same dates of the five years before, for the board report
//   net_months  net income by month, this year and the five before, for the board report's chart
// Runs on the machine that holds the QuickBooks connection (the qbo-mcp install and its token
// store); the web app never talks to QuickBooks. QBO_MCP_DIR points at that install. Read only (writes are qbo_write.mjs).
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

// The balance sheet today, to two levels (Assets > Current Assets > Bank Accounts): [{depth, name, amount}]
const bs = await get(`reports/BalanceSheet?date_macro=Today&accounting_method=Accrual`);
const balance_sheet = [];
const bwalk = (rows, depth) => {
  for (const r of rows || []) {
    if (r.type === 'Section' || r.Rows) {
      const name = r.Header?.ColData?.[0]?.value || r.Summary?.ColData?.[0]?.value?.replace(/^Total /, '') || '';
      const amount = parseFloat(r.Summary?.ColData?.[1]?.value || '0');
      if (depth <= 2 && name) balance_sheet.push({ depth, name, amount });
      if (depth < 2) bwalk(r.Rows?.Row, depth + 1);
    } else if (r.ColData && depth <= 2) {
      const amount = parseFloat(r.ColData[1]?.value || '0');
      if (amount) balance_sheet.push({ depth, name: r.ColData[0].value, amount });
    }
  }
};
bwalk(bs.Rows?.Row, 0);

// Every invoice still owed in QuickBooks, for the treasurer's report.
const receivables = (await query('Invoice', "WHERE Balance > '0'")).map(i => ({
  number: i.DocNumber || '', date: i.TxnDate, due: i.DueDate || '', customer: i.CustomerRef?.name || '', total: i.TotalAmt, balance: i.Balance,
}));

// The profit and loss statement for the board, current to the day: this month so far and the year to date through
// today (the region's day, Detroit time), every line of QuickBooks' report (headings, accounts, subtotals).
const pEnd = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Detroit' });
const pm = pEnd.slice(0, 7), pmEnd = pEnd;
const pnlRep = await get(`reports/ProfitAndLoss?start_date=${pm.slice(0, 4)}-01-01&end_date=${pmEnd}&accounting_method=Accrual&summarize_column_by=Month`);
const pnl = [];
const amt = (cols) => [parseFloat(cols.at(-2)?.value || '0') || 0, parseFloat(cols.at(-1)?.value || '0') || 0];   // [last month, total]
const pwalk = (rows, depth) => {
  for (const r of rows || []) {
    if (r.Header) pnl.push({ period: pEnd, kind: 'head', depth, label: r.Header.ColData[0].value, month: null, ytd: null });
    if (r.ColData) { const [m, y] = amt(r.ColData); pnl.push({ period: pEnd, kind: 'line', depth, label: r.ColData[0].value, month: m, ytd: y }); }
    if (r.Rows) pwalk(r.Rows.Row, depth + 1);
    if (r.Summary) { const [m, y] = amt(r.Summary.ColData); pnl.push({ period: pEnd, kind: 'total', depth, label: r.Summary.ColData[0].value, month: m, ytd: y }); }
  }
};
pwalk(pnlRep.Rows?.Row, 0);
// The month column runs from the first of last month through today (Oct 1 to Nov 11 for a report on Nov 11).
const pStart = new Date(Date.UTC(Number(pEnd.slice(0, 4)), Number(pEnd.slice(5, 7)) - 2, 1)).toISOString().slice(0, 10);
const recent = {};
const rwalk = rows => { for (const r of rows || []) {
  if (r.ColData) recent[`line|${r.ColData[0].value}`] = parseFloat(r.ColData[1]?.value || '0') || 0;
  if (r.Rows) rwalk(r.Rows.Row);
  if (r.Summary) recent[`total|${r.Summary.ColData[0].value}`] = parseFloat(r.Summary.ColData[1]?.value || '0') || 0;
} };
rwalk((await get(`reports/ProfitAndLoss?start_date=${pStart}&end_date=${pEnd}&accounting_method=Accrual`)).Rows?.Row);
for (const r of pnl) {
  if (r.kind !== 'head') r.month = recent[`${r.kind}|${r.label}`] ?? 0;
  r.from = pStart;
}
// The same dates (January 1 to today's month and day) of each of the five years before, for comparison: each line's average over the years it existed
// (the accounts were reorganized in 2022, so older years only share the totals).
const past = {};
for (let k = 1; k <= 5; k++) {
  // the same dates in that year (Feb 29 becomes Feb 28)
  const y = Number(pm.slice(0, 4)) - k, last = new Date(Date.UTC(y, Number(pm.slice(5, 7)), 0)).getUTCDate();
  const end = `${y}-${pm.slice(5, 7)}-${String(Math.min(Number(pEnd.slice(8, 10)), last)).padStart(2, '0')}`;
  const rep = await get(`reports/ProfitAndLoss?start_date=${y}-01-01&end_date=${end}&accounting_method=Accrual`);
  const w = rows => { for (const r of rows || []) {
    const cd = r.ColData || r.Summary?.ColData;
    if (r.ColData) (past[`line|${cd[0].value}`] ||= []).push(parseFloat(cd[1]?.value || '0') || 0);
    if (r.Rows) w(r.Rows.Row);
    if (r.Summary) (past[`total|${r.Summary.ColData[0].value}`] ||= []).push(parseFloat(r.Summary.ColData[1]?.value || '0') || 0);
  } };
  w(rep.Rows?.Row);
}
// Net income by month, this year through today and each of the five years before, for the chart of
// the year so far against the plan and the five-year average.
const netRow = (rep) => {      // the month columns only (a report also ends with a Total column)
  const n = (rep.Columns?.Column || []).filter(c => c.ColTitle && c.ColTitle !== 'Total').length;
  let out = null; const w = rows => { for (const r of rows || []) { if (r.Rows) w(r.Rows.Row);
    if (r.Summary?.ColData?.[0]?.value === 'Net Income') out = r.Summary.ColData.slice(1, 1 + n).map(d => parseFloat(d.value || '0') || 0); } };
  w(rep.Rows?.Row); return out || []; };
const net_months = [{ year: Number(pm.slice(0, 4)), months: netRow(pnlRep) }];
for (let k = 1; k <= 5; k++) {
  const y = Number(pm.slice(0, 4)) - k;
  net_months.push({ year: y, months: netRow(await get(`reports/ProfitAndLoss?start_date=${y}-01-01&end_date=${y}-12-31&accounting_method=Accrual&summarize_column_by=Month`)) });
}
for (const r of pnl) {
  const v = past[`${r.kind}|${r.label}`];
  r.avg5 = v ? Math.round(v.reduce((a, b) => a + b, 0) / v.length * 100) / 100 : null;
  r.avg_years = v ? v.length : 0;
}

console.log(JSON.stringify({ as_of: today, projects, programs, monthly, balances, payments, outstanding, deposits, balance_sheet, receivables, pnl, net_months }));
