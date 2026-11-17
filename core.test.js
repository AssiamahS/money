// Mirrors test_core.py so the browser-only build behaves like the Python server.
const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("./core.js");

const TODAY = "2026-11-17";

const CARD_CSV = `Transaction Date,Clearing Date,Description,Merchant,Category,Type,Amount (USD),Purchased By
11/10/2026,11/11/2026,"UBER *EATS HELP.UBER.COM","Uber Eats","Restaurants","Purchase","23.50","Me"
11/09/2026,11/10/2026,"ACH DEPOSIT INTERNET TRANSFER","Ach Deposit","Payment","Payment","-50.00","Me"
11/08/2026,11/09/2026,"WAWA 8012","Wawa, Inc","Gas","Purchase","31.07","Me"
`;

const BANK_CSV = `Date,Description,Amount
2026-11-01,DUNKIN #3312,-4.89
2026-11-02,PAYROLL,612.40
`;

test("goal with partial payments gives days left and pace", () => {
  const v = core.obligationView(
    {amount: 200, due: "2026-12-18", payments: [{date: TODAY, amount: 45}]}, TODAY);
  assert.equal(v.days_left, 31);
  assert.equal(v.remaining, 155);
  assert.equal(v.per_day, 5);
  assert.equal(v.per_week, 31);
  assert.equal(v.status, "ok");
});

test("overdue, paid, due today and unknown amounts", () => {
  assert.equal(core.obligationView({amount: 10.44, due: "2026-11-14"}, TODAY).status, "overdue");
  assert.equal(core.obligationView({amount: 10.44, due: "2026-11-14"}, TODAY).days_left, -3);
  const paid = core.obligationView(
    {amount: 10.44, due: "2026-11-14", payments: [{amount: 10.44}]}, TODAY);
  assert.equal(paid.status, "paid");
  assert.equal(paid.remaining, 0);
  const today = core.obligationView({amount: 60, due: TODAY}, TODAY);
  assert.equal(today.per_day, 60);
  assert.equal(today.status, "soon");
  assert.equal(core.obligationView({amount: null, due: null}, TODAY).status, "unknown");
  assert.equal(core.obligationView({amount: 100, due: null}, TODAY).status, "no_date");
});

test("card export: quoted commas, purchases positive, payments flagged", () => {
  const rows = core.parseCsv(CARD_CSV, "Apple Card");
  assert.equal(rows.length, 3);
  assert.deepEqual(
    {date: rows[0].date, merchant: rows[0].merchant, category: rows[0].category,
     amount: rows[0].amount, kind: rows[0].kind},
    {date: "2026-11-10", merchant: "Uber Eats", category: "Restaurants", amount: 23.5, kind: "spend"});
  assert.equal(rows[1].kind, "payment");
  assert.equal(rows[2].merchant, "Wawa, Inc");
});

test("bank export: negative is spend, positive is income, category guessed", () => {
  const rows = core.parseCsv(BANK_CSV, "Bank");
  assert.deepEqual([rows[0].kind, rows[0].amount, rows[0].category], ["spend", 4.89, "Coffee & food"]);
  assert.deepEqual([rows[1].kind, rows[1].amount], ["income", 612.4]);
  assert.throws(() => core.parseCsv("foo,bar\n1,2\n", "x"), /date, description and amount/);
});

test("ops: import twice adds nothing new, summary and totals add up", () => {
  const ledger = core.emptyLedger();
  assert.deepEqual(core.applyOp(ledger, "/api/import", {source: "Apple Card", csv: CARD_CSV}),
    {read: 3, added: 3});
  assert.equal(core.applyOp(ledger, "/api/import", {source: "Apple Card", csv: CARD_CSV}).added, 0);
  core.applyOp(ledger, "/api/import", {source: "Bank", csv: BANK_CSV});
  core.applyOp(ledger, "/api/obligation", {name: "Deposit", amount: "200", due: "2026-12-18"});
  core.applyOp(ledger, "/api/obligation", {name: "Late one", amount: "$10.44", due: "2026-11-14"});
  core.applyOp(ledger, "/api/obligation", {name: "Mystery", amount: ""});
  core.applyOp(ledger, "/api/obligation/pay", {id: ledger.obligations[0].id, amount: 45});

  const s = core.buildState(ledger, null, TODAY);
  assert.equal(s.spending.spent, 59.46);
  assert.equal(s.spending.income, 612.4);
  assert.deepEqual(s.spending.by_category[0], {name: "Gas", amount: 31.07});
  assert.equal(s.obligations[0].name, "Late one"); // overdue sorts first
  assert.deepEqual(s.totals, {owed: 165.44, overdue: 10.44, next_30: 10.44, unknown: 1});

  core.applyOp(ledger, "/api/txns/clear", {source: "Bank"});
  assert.equal(ledger.txns.length, 3);
});

test("ops reject bad input and keep only the last four card digits", () => {
  const ledger = core.emptyLedger();
  assert.throws(() => core.applyOp(ledger, "/api/obligation", {name: ""}), /name is required/);
  assert.throws(() => core.applyOp(ledger, "/api/obligation", {name: "x", amount: "-5"}), /positive/);
  assert.throws(() => core.applyOp(ledger, "/api/obligation", {name: "x", due: "soon"}), /date/);
  core.applyOp(ledger, "/api/card", {label: "Music", last4: "4111 1111 1111 1234", limit: "15"});
  assert.equal(ledger.cards[0].last4, "1234");
  assert.equal(ledger.cards[0].limit, 15);
});
