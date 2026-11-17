# money

One page that answers three questions: what do I owe and how many days do I have, which
throwaway card is tied to which merchant, and where did the money go this month.

Live: https://assiamahs.github.io/money/

## Two ways to run it

**In the browser (GitHub Pages, phone).** Nothing to install. The ledger lives in that
browser's `localStorage` and is never uploaded. First visit shows sample numbers; hit
"Start with my own numbers" to clear them.

**On your own machine.** Same page, but the ledger is a JSON file on disk.

```sh
python3 server.py        # http://127.0.0.1:8803, stdlib only, binds to localhost
```

The ledger is written to `../money-ledger.json` (override with `MONEY_LEDGER=/path/file.json`).

## What it does

- **What I owe** - amount, due date, days left, and the per-day / per-week pace to get there.
  Log partial payments; mark rows as estimates until you have the real balance.
- **Temp cards** - a registry (label, issuer, last 4, merchant lock, limit). It does not issue
  cards; the numbers come from your bank or a service like Privacy. Never store full numbers.
- **Where it goes** - import a statement CSV (Apple Card export, or any CSV with date,
  description and amount columns) and get spend by category and merchant per month.
  Re-importing the same file adds nothing.

## Reading email (local version only)

`mailscan.py` reads notification emails and files them for you: upcoming and failed payments
and cash advances land under "What I owe", receipts and renewal notices land under
"Subscriptions", and "payment received" mails log a payment against the matching row.
Each email is applied once, so rescans are safe and a row you delete stays deleted.

```sh
# ~/finance/.env  (Google account -> Security -> App passwords)
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

With that file in place the server sweeps the mailbox every 3 hours and the page gets a
"Scan email now" button. Read-only IMAP; nothing is sent, moved or marked as read.

## Tests

```sh
python3 -m unittest test_core test_mailscan   # server-side logic + email rules
node --test core.test.js          # browser-side twin of the same logic
```

`core.js` mirrors `core.py`; change one, change the other, run both.
