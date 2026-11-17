#!/usr/bin/env python3
"""Money dashboard: what is owed and by when, temp-card registry, where the money goes.

Local only (127.0.0.1). Data lives in ~/finance/money-ledger.json, which is gitignored.
Run: python3 ~/finance/money/server.py   then open http://127.0.0.1:8803
"""
import json
import os
import sys
import uuid
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import core

HOST, PORT = "127.0.0.1", 8803
HERE = Path(__file__).resolve().parent
LEDGER = Path(os.environ.get("MONEY_LEDGER", HERE.parent / "money-ledger.json"))
BALANCE_HISTORY = HERE.parent / "history.jsonl"
MAX_BODY = 5 * 1024 * 1024
STATUS_ORDER = {"overdue": 0, "soon": 1, "ok": 2, "no_date": 3, "unknown": 4, "paid": 5}
EMPTY = {"obligations": [], "cards": [], "txns": []}


def load():
    if LEDGER.exists():
        return {**EMPTY, **json.loads(LEDGER.read_text())}
    return json.loads(json.dumps(EMPTY))


def save(ledger):
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(LEDGER)


def latest_balances():
    """Last line the Plaid watchdog (check.py) wrote, if it has real accounts."""
    try:
        last = BALANCE_HISTORY.read_text().strip().splitlines()[-1]
        row = json.loads(last)
    except (OSError, IndexError, ValueError):
        return None
    return {"date": row.get("date"), "total": row.get("total"),
            "accounts": row.get("accounts", []), "alerts": row.get("alerts", [])}


def state(month=None):
    ledger = load()
    today = date.today()
    obligations = [{**ob, **core.obligation_view(ob, today)} for ob in ledger["obligations"]]
    obligations.sort(key=lambda o: (STATUS_ORDER[o["status"]],
                                    o["days_left"] if o["days_left"] is not None else 9999))
    open_known = [o for o in obligations if o["remaining"]]
    return {
        "today": today.isoformat(),
        "obligations": obligations,
        "totals": {
            "owed": round(sum(o["remaining"] for o in open_known), 2),
            "overdue": round(sum(o["remaining"] for o in open_known
                                 if o["status"] == "overdue"), 2),
            "next_30": round(sum(o["remaining"] for o in open_known
                                 if o["days_left"] is not None and o["days_left"] <= 30), 2),
            "unknown": sum(1 for o in obligations if o["status"] == "unknown"),
        },
        "cards": ledger["cards"],
        "spending": core.summarize(ledger["txns"], month),
        "recent": [t for t in ledger["txns"]
                   if not month or t["date"][:7] == month][:40],
        "balances": latest_balances(),
    }


def clean_text(value, limit=200):
    return str(value or "").strip()[:limit]


def clean_amount(value):
    if value in (None, ""):
        return None
    amount = core.parse_money(str(value))
    if amount is None or amount < 0:
        raise ValueError("amount must be a positive number")
    return round(amount, 2)


def clean_due(value):
    if not value:
        return None
    parsed = core.parse_date(str(value))
    if parsed is None:
        raise ValueError("date must look like 2026-12-18")
    return parsed.isoformat()


def find(items, item_id):
    for item in items:
        if item["id"] == item_id:
            return item
    raise ValueError("not found")


def op_obligation_save(ledger, body):
    fields = {"name": clean_text(body.get("name"), 80), "amount": clean_amount(body.get("amount")),
              "due": clean_due(body.get("due")), "note": clean_text(body.get("note"), 400),
              "verified": bool(body.get("verified", True))}
    if not fields["name"]:
        raise ValueError("name is required")
    if body.get("id"):
        find(ledger["obligations"], body["id"]).update(fields)
    else:
        ledger["obligations"].append({"id": uuid.uuid4().hex[:8], "payments": [], **fields})


def op_obligation_pay(ledger, body):
    amount = clean_amount(body.get("amount"))
    if not amount:
        raise ValueError("amount is required")
    ob = find(ledger["obligations"], body.get("id"))
    ob.setdefault("payments", []).append(
        {"date": clean_due(body.get("date")) or date.today().isoformat(), "amount": amount})


def op_obligation_undo(ledger, body):
    payments = find(ledger["obligations"], body.get("id")).get("payments", [])
    if payments:
        payments.pop()


def op_obligation_delete(ledger, body):
    ledger["obligations"].remove(find(ledger["obligations"], body.get("id")))


def op_card_save(ledger, body):
    last4 = "".join(ch for ch in clean_text(body.get("last4"), 8) if ch.isdigit())[-4:]
    fields = {"label": clean_text(body.get("label"), 60),
              "provider": clean_text(body.get("provider"), 40), "last4": last4,
              "merchant": clean_text(body.get("merchant"), 60),
              "limit": clean_amount(body.get("limit")),
              "status": body.get("status") if body.get("status") in ("active", "paused", "closed")
              else "active",
              "note": clean_text(body.get("note"), 200)}
    if not fields["label"]:
        raise ValueError("label is required")
    if body.get("id"):
        find(ledger["cards"], body["id"]).update(fields)
    else:
        ledger["cards"].append({"id": uuid.uuid4().hex[:8], **fields})


def op_card_delete(ledger, body):
    ledger["cards"].remove(find(ledger["cards"], body.get("id")))


def op_import(ledger, body):
    source = clean_text(body.get("source"), 40) or "Statement"
    rows = core.parse_csv(str(body.get("csv") or ""), source)
    ledger["txns"], added = core.merge_txns(ledger["txns"], rows)
    return {"read": len(rows), "added": added}


def op_txns_clear(ledger, body):
    source = clean_text(body.get("source"), 40)
    if not source:
        raise ValueError("source is required")
    ledger["txns"] = [t for t in ledger["txns"] if t["source"] != source]


OPS = {
    "/api/obligation": op_obligation_save,
    "/api/obligation/pay": op_obligation_pay,
    "/api/obligation/undo": op_obligation_undo,
    "/api/obligation/delete": op_obligation_delete,
    "/api/card": op_card_save,
    "/api/card/delete": op_card_delete,
    "/api/import": op_import,
    "/api/txns/clear": op_txns_clear,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "money/1"

    def log_message(self, *args):
        pass

    def reply(self, code, payload, content_type="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def local_only(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self.local_only():
            return self.reply(403, {"error": "local only"})
        path, _, query = self.path.partition("?")
        if path == "/":
            return self.reply(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/core.js":
            return self.reply(200, (HERE / "core.js").read_bytes(), "text/javascript; charset=utf-8")
        if path == "/api/state":
            month = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("month")
            return self.reply(200, state(month))
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        # JSON-only bodies: a cross-site form post cannot send this content type.
        if not self.local_only() or "application/json" not in (
                self.headers.get("Content-Type") or ""):
            return self.reply(403, {"error": "local JSON requests only"})
        op = OPS.get(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if op is None or length > MAX_BODY:
            return self.reply(404 if op is None else 413, {"error": "bad request"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            ledger = load()
            result = op(ledger, body) or {}
            save(ledger)
        except (ValueError, KeyError, TypeError) as err:
            return self.reply(400, {"error": str(err)})
        self.reply(200, {"ok": True, **result, "state": state()})


if __name__ == "__main__":
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except OSError as err:
        sys.exit(f"cannot bind {HOST}:{PORT}: {err}")
