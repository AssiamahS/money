// Browser twin of core.py + the server's write operations, so the page works with no backend
// (GitHub Pages, phone) by keeping the ledger in localStorage. Keep in step with core.py.
(function (root) {
  "use strict";

  const SOON_DAYS = 7;
  const STATUS_ORDER = {overdue: 0, soon: 1, ok: 2, no_date: 3, unknown: 4, paid: 5};
  const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
  const CATEGORY_RULES = [
    ["Coffee & food", ["dunkin", "starbucks", "wawa", "mcdonald", "chipotle", "uber eats",
      "doordash", "grubhub", "pizza", "taco", "wendy", "popeyes", "chick-fil"]],
    ["Groceries", ["shoprite", "aldi", "walmart", "target", "costco", "stop & shop", "acme",
      "trader joe", "whole foods", "instacart"]],
    ["Gas & transport", ["exxon", "shell", "sunoco", "bp ", "uber", "lyft", "nj transit",
      "ezpass", "e-zpass", "parking"]],
    ["Subscriptions", ["apple.com/bill", "spotify", "netflix", "hulu", "google", "openai",
      "youtube", "icloud", "adobe", "github"]],
    ["Cash advance & fees", ["dave", "albert", "earnin", "money app", "klarna", "afterpay",
      "affirm", "spotme", "brigit", "cleo", "moneylion", "fee"]],
    ["Phone & bills", ["verizon", "t-mobile", "at&t", "comcast", "xfinity", "pse&g", "jcp&l"]],
    ["Shopping", ["amazon", "amzn", "best buy", "ebay", "nike", "ticketmaster"]],
  ];

  const round2 = (n) => Math.round((n + Number.EPSILON) * 100) / 100;
  const pad = (n) => String(n).padStart(2, "0");

  function iso(y, m, d) {
    const t = new Date(Date.UTC(y, m - 1, d));
    const real = t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
    return real ? `${y}-${pad(m)}-${pad(d)}` : null;
  }

  function parseDate(text) {
    const s = String(text || "").trim();
    let m;
    if ((m = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/.exec(s))) return iso(+m[1], +m[2], +m[3]);
    if ((m = /^(\d{1,2})\/(\d{1,2})\/(\d{4}|\d{2})$/.exec(s))) {
      return iso(m[3].length === 2 ? 2000 + +m[3] : +m[3], +m[1], +m[2]);
    }
    if ((m = /^([A-Za-z]{3})[a-z]* (\d{1,2}), (\d{4})$/.exec(s))) {
      const month = MONTHS.indexOf(m[1].toLowerCase());
      return month < 0 ? null : iso(+m[3], month + 1, +m[2]);
    }
    return null;
  }

  function parseMoney(text) {
    let s = String(text == null ? "" : text).trim().replace(/[$,]/g, "");
    if (/^\(.*\)$/.test(s)) s = "-" + s.slice(1, -1);
    return /^-?(\d+\.?\d*|\.\d+)$/.test(s) ? parseFloat(s) : null;
  }

  function todayISO() {
    const now = new Date();
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  }

  const dayNumber = (isoDate) => Date.parse(isoDate + "T00:00:00Z") / 86400000;

  function obligationView(ob, today) {
    const amount = ob.amount == null ? null : ob.amount;
    const paid = round2((ob.payments || []).reduce((sum, p) => sum + (p.amount || 0), 0));
    const due = ob.due ? parseDate(ob.due) : null;
    const remaining = amount === null ? null : Math.max(round2(amount - paid), 0);
    const daysLeft = due ? Math.round(dayNumber(due) - dayNumber(today || todayISO())) : null;

    let perDay = null, perWeek = null;
    if (remaining && daysLeft !== null && daysLeft >= 0) {
      perDay = round2(remaining / Math.max(daysLeft, 1));
      perWeek = round2(remaining / Math.max(Math.ceil(daysLeft / 7), 1));
    }

    let status = "ok";
    if (amount === null) status = "unknown";
    else if (remaining === 0) status = "paid";
    else if (daysLeft === null) status = "no_date";
    else if (daysLeft < 0) status = "overdue";
    else if (daysLeft <= SOON_DAYS) status = "soon";

    return {paid, remaining, days_left: daysLeft, per_day: perDay, per_week: perWeek, status};
  }

  function categorize(description) {
    const low = description.toLowerCase();
    const hit = CATEGORY_RULES.find(([, needles]) => needles.some((n) => low.includes(n)));
    return hit ? hit[0] : "Other";
  }

  // RFC 4180-style: quoted fields, doubled quotes, commas and newlines inside quotes.
  function csvRows(text) {
    const rows = [];
    let row = [], field = "", quoted = false;
    const src = text.replace(/^﻿/, "");
    for (let i = 0; i < src.length; i++) {
      const ch = src[i];
      if (quoted) {
        if (ch === '"' && src[i + 1] === '"') { field += '"'; i++; }
        else if (ch === '"') quoted = false;
        else field += ch;
      } else if (ch === '"') quoted = true;
      else if (ch === ",") { row.push(field); field = ""; }
      else if (ch === "\n" || ch === "\r") {
        if (ch === "\r" && src[i + 1] === "\n") i++;
        row.push(field); field = "";
        if (row.some((cell) => cell !== "")) rows.push(row);
        row = [];
      } else field += ch;
    }
    row.push(field);
    if (row.some((cell) => cell !== "")) rows.push(row);
    return rows;
  }

  function findColumn(headers, candidates) {
    const low = headers.map((h) => h.trim().toLowerCase());
    for (const cand of candidates) {
      const exact = low.indexOf(cand);
      if (exact >= 0) return exact;
    }
    for (const cand of candidates) {
      const loose = low.findIndex((h) => h.includes(cand));
      if (loose >= 0) return loose;
    }
    return -1;
  }

  function parseCsv(text, source) {
    const [headers = [], ...lines] = csvRows(String(text || ""));
    const dateCol = findColumn(headers, ["transaction date", "date", "posted"]);
    const amountCol = findColumn(headers, ["amount (usd)", "amount"]);
    const descCol = findColumn(headers, ["merchant", "description", "name", "memo"]);
    if (dateCol < 0 || amountCol < 0 || descCol < 0) {
      throw new Error("CSV needs date, description and amount columns; got: " + headers.join(", "));
    }
    const categoryCol = findColumn(headers, ["category"]);
    const typeCol = findColumn(headers, ["type"]);
    const cardStyle = headers[amountCol].toLowerCase().includes("amount (usd)");

    const out = [];
    for (const cells of lines) {
      const when = parseDate(cells[dateCol]);
      const value = parseMoney(cells[amountCol]);
      if (when === null || value === null) continue;
      const merchant = (cells[descCol] || "").trim() || "Unknown";
      const hint = typeCol >= 0 ? (cells[typeCol] || "").trim().toLowerCase() : "";
      let kind;
      if (cardStyle) kind = value < 0 || hint === "payment" || hint === "credit" ? "payment" : "spend";
      else kind = value < 0 ? "spend" : "income";
      let category = categoryCol >= 0 ? (cells[categoryCol] || "").trim() : "";
      if (!category || category.toLowerCase() === "other") category = categorize(merchant);
      out.push({date: when, merchant: merchant.slice(0, 80), category: category.slice(0, 40),
        amount: round2(Math.abs(value)), kind, source: String(source).slice(0, 40)});
    }
    return out;
  }

  function mergeTxns(existing, incoming) {
    const known = new Set(existing.map((t) => t.id));
    const seen = new Map();
    const merged = existing.slice();
    let added = 0;
    for (const txn of incoming) {
      const base = [txn.source, txn.date, txn.merchant, txn.amount.toFixed(2), txn.kind].join("|");
      const nth = seen.get(base) || 0;
      seen.set(base, nth + 1);
      const id = base + "|" + nth;
      if (known.has(id)) continue;
      merged.push({...txn, id});
      known.add(id);
      added++;
    }
    merged.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
    return [merged, added];
  }

  function ranked(totals, limit) {
    const list = Object.entries(totals).map(([name, amount]) => ({name, amount: round2(amount)}))
      .sort((a, b) => b.amount - a.amount);
    return limit ? list.slice(0, limit) : list;
  }

  function summarize(txns, month) {
    const months = [...new Set(txns.map((t) => t.date.slice(0, 7)))].sort().reverse();
    const picked = month || months[0] || null;
    const scoped = picked ? txns.filter((t) => t.date.slice(0, 7) === picked) : [];
    const byCat = {}, byMerchant = {}, bySource = {};
    let spent = 0, income = 0;
    for (const t of scoped) {
      if (t.kind === "spend") {
        spent += t.amount;
        byCat[t.category] = (byCat[t.category] || 0) + t.amount;
        byMerchant[t.merchant] = (byMerchant[t.merchant] || 0) + t.amount;
        bySource[t.source] = (bySource[t.source] || 0) + t.amount;
      } else if (t.kind === "income") income += t.amount;
    }
    return {month: picked, months, spent: round2(spent), income: round2(income),
      by_category: ranked(byCat), by_merchant: ranked(byMerchant, 10),
      by_source: ranked(bySource), count: scoped.length};
  }

  const emptyLedger = () => ({obligations: [], cards: [], txns: [], subs: []});

  function buildState(ledger, month, today) {
    const day = today || todayISO();
    const obligations = ledger.obligations.map((ob) => ({...ob, ...obligationView(ob, day)}));
    const far = (o) => (o.days_left === null ? 9999 : o.days_left);
    obligations.sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || far(a) - far(b));
    const open = obligations.filter((o) => o.remaining);
    const total = (list) => round2(list.reduce((sum, o) => sum + o.remaining, 0));
    return {
      today: day,
      obligations,
      totals: {
        owed: total(open),
        overdue: total(open.filter((o) => o.status === "overdue")),
        next_30: total(open.filter((o) => o.days_left !== null && o.days_left <= 30)),
        unknown: obligations.filter((o) => o.status === "unknown").length,
      },
      cards: ledger.cards,
      subs: ledger.subs || [],
      subs_total: round2((ledger.subs || []).reduce((sum, s) => sum + (s.amount || 0), 0)),
      mail: {connected: false, last_scan: null, last_result: null},
      spending: summarize(ledger.txns, month),
      recent: ledger.txns.filter((t) => !month || t.date.slice(0, 7) === month).slice(0, 40),
      balances: null,
    };
  }

  const cleanText = (value, limit) => String(value == null ? "" : value).trim().slice(0, limit || 200);

  function cleanAmount(value) {
    if (value === null || value === undefined || value === "") return null;
    const amount = parseMoney(value);
    if (amount === null || amount < 0) throw new Error("amount must be a positive number");
    return round2(amount);
  }

  function cleanDue(value) {
    if (!value) return null;
    const parsed = parseDate(value);
    if (parsed === null) throw new Error("date must look like 2026-12-18");
    return parsed;
  }

  function find(items, id) {
    const item = items.find((it) => it.id === id);
    if (!item) throw new Error("not found");
    return item;
  }

  const newId = () => Math.random().toString(16).slice(2, 10);

  const OPS = {
    "/api/obligation"(ledger, body) {
      const fields = {name: cleanText(body.name, 80), amount: cleanAmount(body.amount),
        due: cleanDue(body.due), note: cleanText(body.note, 400),
        verified: body.verified === undefined ? true : Boolean(body.verified)};
      if (!fields.name) throw new Error("name is required");
      if (body.id) Object.assign(find(ledger.obligations, body.id), fields);
      else ledger.obligations.push({id: newId(), payments: [], ...fields});
    },
    "/api/obligation/pay"(ledger, body) {
      const amount = cleanAmount(body.amount);
      if (!amount) throw new Error("amount is required");
      const ob = find(ledger.obligations, body.id);
      (ob.payments = ob.payments || []).push({date: cleanDue(body.date) || todayISO(), amount});
    },
    "/api/obligation/undo"(ledger, body) {
      (find(ledger.obligations, body.id).payments || []).pop();
    },
    "/api/obligation/delete"(ledger, body) {
      ledger.obligations.splice(ledger.obligations.indexOf(find(ledger.obligations, body.id)), 1);
    },
    "/api/card"(ledger, body) {
      const fields = {label: cleanText(body.label, 60), provider: cleanText(body.provider, 40),
        last4: cleanText(body.last4, 40).replace(/\D/g, "").slice(-4),
        merchant: cleanText(body.merchant, 60), limit: cleanAmount(body.limit),
        status: ["active", "paused", "closed"].includes(body.status) ? body.status : "active",
        note: cleanText(body.note, 200)};
      if (!fields.label) throw new Error("label is required");
      if (body.id) Object.assign(find(ledger.cards, body.id), fields);
      else ledger.cards.push({id: newId(), ...fields});
    },
    "/api/card/delete"(ledger, body) {
      ledger.cards.splice(ledger.cards.indexOf(find(ledger.cards, body.id)), 1);
    },
    "/api/import"(ledger, body) {
      const rows = parseCsv(body.csv, cleanText(body.source, 40) || "Statement");
      const [merged, added] = mergeTxns(ledger.txns, rows);
      ledger.txns = merged;
      return {read: rows.length, added};
    },
    "/api/subscription/delete"(ledger, body) {
      ledger.subs.splice(ledger.subs.indexOf(find(ledger.subs, body.id)), 1);
    },
    "/api/scan"() {
      throw new Error("Reading email needs the version that runs on your own computer");
    },
    "/api/txns/clear"(ledger, body) {
      const source = cleanText(body.source, 40);
      if (!source) throw new Error("source is required");
      ledger.txns = ledger.txns.filter((t) => t.source !== source);
    },
  };

  function applyOp(ledger, path, body) {
    if (!Object.prototype.hasOwnProperty.call(OPS, path)) throw new Error("unknown action");
    return OPS[path](ledger, body || {}) || {};
  }

  const api = {obligationView, parseCsv, summarize, buildState, applyOp, emptyLedger, todayISO};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.MoneyCore = api;
})(typeof self !== "undefined" ? self : this);
