"""Pure logic for the money dashboard: deadline math, statement import, spend summary."""
import csv
import hashlib
import io
import math
from collections import defaultdict
from datetime import date, datetime

SOON_DAYS = 7
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%b %d, %Y")

# Used only when the statement has no category column of its own.
CATEGORY_RULES = (
    ("Coffee & food", ("dunkin", "starbucks", "wawa", "mcdonald", "chipotle", "uber eats",
                       "doordash", "grubhub", "pizza", "taco", "wendy", "popeyes", "chick-fil")),
    ("Groceries", ("shoprite", "aldi", "walmart", "target", "costco", "stop & shop", "acme",
                   "trader joe", "whole foods", "instacart")),
    ("Gas & transport", ("exxon", "shell", "sunoco", "bp ", "uber", "lyft", "nj transit",
                         "ezpass", "e-zpass", "parking")),
    ("Subscriptions", ("apple.com/bill", "spotify", "netflix", "hulu", "google", "openai",
                       "youtube", "icloud", "adobe", "github")),
    ("Cash advance & fees", ("dave", "albert", "earnin", "money app", "klarna", "afterpay",
                             "affirm", "spotme", "brigit", "cleo", "moneylion", "fee")),
    ("Phone & bills", ("verizon", "t-mobile", "at&t", "comcast", "xfinity", "pse&g", "jcp&l")),
    ("Shopping", ("amazon", "amzn", "best buy", "ebay", "nike", "ticketmaster")),
)


def parse_date(text):
    text = (text or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(text):
    text = (text or "").strip().replace("$", "").replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def obligation_view(ob, today=None):
    """Remaining balance, days left and the daily/weekly pace needed to hit the due date."""
    today = today or date.today()
    amount = ob.get("amount")
    paid = round(sum(p.get("amount", 0) for p in ob.get("payments", [])), 2)
    due = parse_date(ob.get("due")) if ob.get("due") else None
    remaining = None if amount is None else max(round(amount - paid, 2), 0)
    days_left = (due - today).days if due else None

    per_day = per_week = None
    if remaining and days_left is not None and days_left >= 0:
        per_day = round(remaining / max(days_left, 1), 2)
        per_week = round(remaining / max(math.ceil(days_left / 7), 1), 2)

    if amount is None:
        status = "unknown"
    elif remaining == 0:
        status = "paid"
    elif days_left is None:
        status = "no_date"
    elif days_left < 0:
        status = "overdue"
    elif days_left <= SOON_DAYS:
        status = "soon"
    else:
        status = "ok"

    return {"paid": paid, "remaining": remaining, "days_left": days_left,
            "per_day": per_day, "per_week": per_week, "status": status}


def categorize(description):
    low = description.lower()
    for name, needles in CATEGORY_RULES:
        if any(n in low for n in needles):
            return name
    return "Other"


def _find_column(headers, *candidates):
    lowered = [h.strip().lower() for h in headers]
    for cand in candidates:
        for i, h in enumerate(lowered):
            if h == cand:
                return headers[i]
    for cand in candidates:
        for i, h in enumerate(lowered):
            if cand in h:
                return headers[i]
    return None


def parse_csv(text, source):
    """Read an Apple Card export or a plain bank CSV into normalized transactions.

    Apple Card lists purchases as positive numbers; bank exports list them as negative.
    Either way the result carries a positive amount plus kind = spend | payment | income.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = reader.fieldnames or []
    date_col = _find_column(headers, "transaction date", "date", "posted")
    amount_col = _find_column(headers, "amount (usd)", "amount")
    desc_col = _find_column(headers, "merchant", "description", "name", "memo")
    if not (date_col and amount_col and desc_col):
        raise ValueError("CSV needs date, description and amount columns; got: "
                         + ", ".join(headers))
    category_col = _find_column(headers, "category")
    type_col = _find_column(headers, "type")
    card_style = "amount (usd)" in amount_col.lower()

    rows = []
    for raw in reader:
        when = parse_date(raw.get(date_col))
        value = parse_money(raw.get(amount_col))
        if when is None or value is None:
            continue
        merchant = (raw.get(desc_col) or "").strip() or "Unknown"
        kind_hint = (raw.get(type_col) or "").strip().lower() if type_col else ""
        if card_style:
            kind = "payment" if value < 0 or kind_hint in ("payment", "credit") else "spend"
        else:
            kind = "spend" if value < 0 else "income"
        category = (raw.get(category_col) or "").strip() if category_col else ""
        if not category or category.lower() == "other":
            category = categorize(merchant)
        rows.append({"date": when.isoformat(), "merchant": merchant[:80],
                     "category": category[:40], "amount": round(abs(value), 2),
                     "kind": kind, "source": source[:40]})
    return rows


def _txn_key(txn, nth):
    raw = "|".join([txn["source"], txn["date"], txn["merchant"],
                    f'{txn["amount"]:.2f}', txn["kind"], str(nth)])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def merge_txns(existing, incoming):
    """Append new transactions, skipping ones already imported. Returns (all, added_count)."""
    known = {t["id"] for t in existing}
    seen_in_batch = defaultdict(int)
    merged, added = list(existing), 0
    for txn in incoming:
        base = (txn["source"], txn["date"], txn["merchant"], txn["amount"], txn["kind"])
        nth = seen_in_batch[base]
        seen_in_batch[base] += 1
        key = _txn_key(txn, nth)
        if key in known:
            continue
        merged.append({**txn, "id": key})
        known.add(key)
        added += 1
    merged.sort(key=lambda t: t["date"], reverse=True)
    return merged, added


def _ranked(totals, limit=None):
    ranked = sorted(({"name": k, "amount": round(v, 2)} for k, v in totals.items()),
                    key=lambda r: r["amount"], reverse=True)
    return ranked[:limit] if limit else ranked


def summarize(txns, month=None):
    months = sorted({t["date"][:7] for t in txns}, reverse=True)
    month = month or (months[0] if months else None)
    scoped = [t for t in txns if t["date"][:7] == month] if month else []
    by_cat, by_merchant, by_source = defaultdict(float), defaultdict(float), defaultdict(float)
    spent = income = 0.0
    for t in scoped:
        if t["kind"] == "spend":
            spent += t["amount"]
            by_cat[t["category"]] += t["amount"]
            by_merchant[t["merchant"]] += t["amount"]
            by_source[t["source"]] += t["amount"]
        elif t["kind"] == "income":
            income += t["amount"]
    return {"month": month, "months": months, "spent": round(spent, 2),
            "income": round(income, 2), "by_category": _ranked(by_cat),
            "by_merchant": _ranked(by_merchant, 10), "by_source": _ranked(by_source),
            "count": len(scoped)}
