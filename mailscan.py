#!/usr/bin/env python3
"""Turn notification emails into ledger rows: bills due, failed payments, advances, subscriptions.

classify(message)            -> findings for one email (pure, no I/O)
scan_messages(ledger, msgs)  -> applies findings to the ledger, idempotent per message id
fetch_imap(user, password)   -> pulls recent mail from Gmail over IMAP

CLI:
  python3 mailscan.py --json messages.json   # feed messages exported by something else
  python3 mailscan.py --imap                 # needs GMAIL_USER / GMAIL_APP_PASSWORD in ~/finance/.env
"""
import email
import email.policy
import email.utils
import html
import imaplib
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

BODY_CHARS = 1200  # legal footers repeat dollar amounts; only the top of the mail is trusted
PERSONAL_DOMAINS = ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "aol.com")
MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}

PROMO = re.compile(r"% off|cashback|refer a friend|black friday|\bboost\b|waiting|finish taking|"
                   r"\bdeals?\b|\bsale\b|is here!", re.I)
NEGATED = re.compile(r"no (past-due|missed|late) payments?[^.]*", re.I)
FAILED = re.compile(
    r"(payment|auto-?renewals?|autopay|charge)[^.]{0,60}?(failed|didn.t go through|declined|"
    r"unsuccessful|could not be processed)|declined a payment|past due|overdue|"
    r"weren.t able to settle|account [\w-]* ?has been (suspended|terminated)", re.I)
PAID = re.compile(
    r"payment (amount )?(of \$[\d,.]+ )?(to \w+ )?(was successful|was received|received|posted)|"
    r"received your payment|thanks for your payment|you.re settled up|paid in full|"
    r"settlement is processing", re.I)
DUE = re.compile(
    r"due date|amount due|payment due|will be charged|will be withdrawn|we will charge|"
    r"upcoming (scheduled )?payment|next (automatic )?payment|complete your deposit|"
    r"make your [\w$ ]{0,20}deposit|will be renewed|upcoming renewal", re.I)
ADVANCE = re.compile(r"congrats on your \$([\d,.]+) loan|\$([\d,.]+) extracash\S* is (here|on the way)|"
                     r"\$([\d,.]+) has landed", re.I)
SUBSCRIPTION = re.compile(r"subscription|membership|renew|auto-renew", re.I)
RECEIPT = re.compile(r"\breceipt\b|you.ve been charged|was used to purchase", re.I)
DATE_HINT = re.compile(r"due|charge|withdrawn|settles|before|expires|renew", re.I)

AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
SETTLEMENT = re.compile(r"settlement amount\s*\$\s?(\d[\d,]*(?:\.\d{1,2})?)", re.I)
LONG_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? (\d{1,2})"
                       r"(?:st|nd|rd|th)?,? (\d{4})", re.I)
SHORT_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")
RELATIVE = re.compile(r"\bin (\d{1,3}) days\b", re.I)
ITEM_FOR = re.compile(r"\bfor ([A-Z][\w&' -]{1,30}?)(?= didn| was| is|\.|$)")
ITEM_BOUGHT = re.compile(r"used to purchase ([^.]{2,50})\.")
ITEM_DOMAIN = re.compile(r"\b([a-z0-9-]{2,40}\.(?:com|dev|net|org|io|app|co|ai))\b")
INVISIBLE = re.compile(r"[​-‏ ͏­﻿⁠]")


def _tidy(text):
    return re.sub(r"\s+", " ", INVISIBLE.sub("", html.unescape(text or ""))).strip()


def _money(match_text):
    return round(float(match_text.replace(",", "")), 2)


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _dates(text, sent):
    """Every date mentioned, as (position, date), including 'in 30 days' relative to the mail."""
    found = []
    for m in LONG_DATE.finditer(text):
        found.append((m.start(), _safe_date(int(m[3]), MONTHS[m[1].lower()], int(m[2]))))
    for m in SHORT_DATE.finditer(text):
        year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        found.append((m.start(), _safe_date(year, int(m[1]), int(m[2]))))
    for m in RELATIVE.finditer(text):
        found.append((m.start(), sent + timedelta(days=int(m[1]))))
    return sorted((pos, d) for pos, d in found if d)


def _date_after_hint(text, sent):
    hint = DATE_HINT.search(text)
    if not hint:
        return None
    later = [d for pos, d in _dates(text, sent) if pos > hint.start()]
    return later[0].isoformat() if later else None


def _merchant(message):
    name = re.split(r" \| |, ", message.get("from_name") or "")[0]
    name = re.sub(r"\s+(support|team|registrar|billing|payments?|alerts?)$", "", name, flags=re.I)
    if name.strip():
        return name.strip()
    domain = (message.get("from_addr") or "unknown").split("@")[-1].split(".")
    return domain[-2].title() if len(domain) > 1 else "Unknown"


def _item(subject, text, sender_addr):
    for pattern, source in ((ITEM_FOR, subject), (ITEM_BOUGHT, text)):
        hit = pattern.search(source)
        if hit:
            return hit[1].strip()
    if SUBSCRIPTION.search(subject + " " + text):
        own = (sender_addr or "").split("@")[-1].lower()
        for hit in ITEM_DOMAIN.finditer((subject + " " + text).lower()):
            if not own.endswith(hit[1]):
                return hit[1]
    return ""


def classify(message):
    """Findings for one email. Each: type (failed|due|paid|advance|sub), name, key, amount, due."""
    subject = _tidy(message.get("subject"))
    addr = (message.get("from_addr") or "").lower()
    if (addr.split("@")[-1] in PERSONAL_DOMAINS
            or "CATEGORY_PROMOTIONS" in (message.get("labels") or ())
            or PROMO.search(subject)):
        return []
    body = _tidy(message.get("text"))[:BODY_CHARS]
    text = NEGATED.sub("", subject + ". " + body)
    sent = date.fromisoformat(message["date"][:10])
    merchant = _merchant(message)
    item = _item(subject, body, addr)
    amount_hit = AMOUNT.search(text)
    amount = _money(amount_hit[1]) if amount_hit else None

    def finding(kind, amount=amount, due=None):
        return {"type": kind, "merchant": merchant, "item": item,
                "name": f"{merchant}: {item}" if item else merchant,
                "key": f"{merchant.split()[0].lower()}|{item.lower()}",
                "amount": amount, "due": due, "date": message["date"],
                "id": message["id"], "subject": subject[:90]}

    if FAILED.search(text):
        return [finding("failed", due=_date_after_hint(text, sent))]

    advance = ADVANCE.search(text)
    if advance:
        settle = SETTLEMENT.search(text)
        owed = _money(settle[1]) if settle else _money(next(g for g in advance.groups()
                                                            if g and g[0].isdigit()))
        return [finding("advance", amount=owed, due=_date_after_hint(text, sent))]

    found = []
    paid = PAID.search(text)
    if paid:
        found.append(finding("paid"))
    remainder = text[paid.end():] if paid else text
    if DUE.search(remainder):
        due = _date_after_hint(remainder, sent)
        if amount is not None or due:
            found.append(finding("sub" if SUBSCRIPTION.search(text) else "due", due=due))
    elif not paid and RECEIPT.search(text):
        found.append(finding("sub"))
    return found


def _open_amount(ob):
    if ob.get("amount") is None:
        return None
    return round(ob["amount"] - sum(p["amount"] for p in ob.get("payments", [])), 2)


def _find_obligation(ledger, found):
    for ob in ledger["obligations"]:
        if ob.get("src", {}).get("key") == found["key"]:
            return ob
    word = found["merchant"].split()[0].lower()
    for ob in ledger["obligations"]:  # adopt a hand-typed row rather than duplicate it
        same_amount = None in (ob.get("amount"), found["amount"]) or ob["amount"] == found["amount"]
        if "src" not in ob and word in ob["name"].lower() and same_amount and (
                not found["item"] or found["item"].lower() in ob["name"].lower()):
            return ob
    return None


def _apply_owed(ledger, found):
    day = found["date"][:10]
    ob = _find_obligation(ledger, found)
    created = ob is None
    if created:
        ob = {"id": uuid.uuid4().hex[:8], "name": found["name"], "amount": None, "due": None,
              "payments": [], "note": ""}
        ledger["obligations"].append(ob)
    new_cycle = found["due"] and found["due"] != ob.get("due") and _open_amount(ob) == 0
    if new_cycle:
        ob["payments"] = []
    if found["amount"] is not None:
        ob["amount"] = found["amount"]
    if found["due"]:
        ob["due"] = found["due"]
    elif found["type"] == "failed" and not ob.get("due"):
        ob["due"] = day
    ob["verified"] = found["type"] != "advance" and ob["amount"] is not None
    ob["note"] = f'From email {day}: {found["subject"]}'
    ob["src"] = {"key": found["key"], "date": found["date"]}
    return created


def _apply_paid(ledger, found):
    if found["amount"] is None:
        return False
    word = found["merchant"].split()[0].lower()
    candidates = [ob for ob in ledger["obligations"]
                  if ob.get("src", {}).get("key", "").startswith(word + "|") and _open_amount(ob) != 0]
    candidates.sort(key=lambda ob: ob["src"]["key"] != found["key"])
    for ob in candidates:
        payment = {"date": found["date"][:10], "amount": found["amount"]}
        if payment in ob["payments"]:
            return False
        if ob["amount"] is None:
            ob["amount"] = found["amount"]
            ob["verified"] = True
        ob["payments"].append(payment)
        return True
    return False


def _apply_sub(ledger, found):
    day = found["date"][:10]
    sub = next((s for s in ledger["subs"] if s["key"] == found["key"]), None)
    created = sub is None
    if created:
        sub = {"id": uuid.uuid4().hex[:8], "key": found["key"], "name": found["name"],
               "amount": None, "next": None, "charges": [], "note": ""}
        ledger["subs"].append(sub)
    if found["amount"] is not None:
        sub["amount"] = found["amount"]
    if found["due"]:
        sub["next"] = found["due"]
    elif day not in sub["charges"]:
        sub["charges"] = sorted(sub["charges"] + [day])
    sub["note"] = f'From email {day}: {found["subject"]}'
    return created


def scan_messages(ledger, messages):
    """Apply every unseen message, oldest first. Safe to call again with the same mail."""
    ledger.setdefault("obligations", [])
    ledger.setdefault("subs", [])
    mail = ledger.setdefault("mail", {"seen": []})
    seen = set(mail["seen"])
    result = {"read": len(messages), "new": 0, "added": 0, "subs": 0, "payments": 0}
    for message in sorted(messages, key=lambda m: m["date"]):
        if message["id"] in seen:
            continue
        seen.add(message["id"])
        findings = classify(message)
        result["new"] += bool(findings)
        for found in findings:
            if found["type"] == "paid":
                result["payments"] += _apply_paid(ledger, found)
            elif found["type"] == "sub":
                result["subs"] += _apply_sub(ledger, found)
            else:
                result["added"] += _apply_owed(ledger, found)
    mail["seen"] = sorted(seen)
    mail["last_scan"] = datetime.now().isoformat(timespec="minutes")
    mail["last_result"] = result
    return result


def _html_to_text(markup):
    markup = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", " ", markup)
    return _tidy(re.sub(r"<[^>]+>", " ", markup))


def message_from_bytes(raw, gmail_msgid):
    """RFC 822 bytes -> message dict. The id matches the Gmail web/API id (hex of X-GM-MSGID)."""
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = email.utils.parseaddr(str(parsed.get("From", "")))
    text = ""
    for kind in ("plain", "html"):
        part = parsed.get_body(preferencelist=(kind,))
        content = part.get_content() if part else ""
        text = _html_to_text(content) if kind == "html" else _tidy(content)
        if text:
            break
    try:
        sent = email.utils.parsedate_to_datetime(str(parsed.get("Date"))).date()
    except (TypeError, ValueError):
        sent = date.today()
    return {"id": format(int(gmail_msgid), "x"), "from_name": name, "from_addr": addr,
            "subject": str(parsed.get("Subject", "")), "text": text,
            "date": sent.isoformat(), "labels": []}


IMAP_QUERY = ('newer_than:{days}d -category:promotions -category:social {{payment receipt renewal '
              'subscription membership "due date" declined overdue deposit loan settlement}}')


def fetch_imap(user, password, days=45, limit=300):
    box = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        box.login(user, password)
        box.select('"[Gmail]/All Mail"', readonly=True)
        _, data = box.uid("SEARCH", "X-GM-RAW", '"%s"' % IMAP_QUERY.format(days=days).replace('"', '\\"'))
        messages = []
        for uid in data[0].split()[-limit:]:
            _, parts = box.uid("FETCH", uid, "(X-GM-MSGID BODY.PEEK[])")
            head, raw = next(p for p in parts if isinstance(p, tuple))
            msgid = re.search(rb"X-GM-MSGID (\d+)", head)
            if msgid:
                messages.append(message_from_bytes(raw, int(msgid[1])))
        return messages
    finally:
        try:
            box.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def mailbox_credentials(env_file=Path.home() / "finance" / ".env"):
    values = dict(os.environ)
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                values[key.strip()] = value.strip().strip('"').strip("'")
    user, password = values.get("GMAIL_USER"), values.get("GMAIL_APP_PASSWORD")
    return (user, password.replace(" ", "")) if user and password else None


def main(argv):
    import server  # late import: server imports this module for its scan endpoint
    if argv[:1] == ["--json"] and len(argv) == 2:
        messages = json.loads(Path(argv[1]).read_text())
    elif argv == ["--imap"]:
        creds = mailbox_credentials()
        if not creds:
            sys.exit("Add GMAIL_USER and GMAIL_APP_PASSWORD to ~/finance/.env first")
        messages = fetch_imap(*creds)
    else:
        sys.exit(__doc__)
    ledger = server.load()
    print(json.dumps(scan_messages(ledger, messages)))
    server.save(ledger)


if __name__ == "__main__":
    main(sys.argv[1:])
