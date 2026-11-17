"""Fixtures are real notification wording (names and ids changed)."""
import unittest
from email.message import EmailMessage

import mailscan

UPDATES = ["CATEGORY_UPDATES"]


def msg(mid, sender, subject, text, date, labels=UPDATES, addr="noreply@example.com"):
    return {"id": mid, "from_name": sender, "from_addr": addr, "subject": subject,
            "text": text, "date": date, "labels": labels}


BNPL_UPCOMING = msg("k1", "Klarna", "Upcoming scheduled payment for Ticketmaster",
                    "Please make sure you have $10.44 available on your card before November 14, 2026. "
                    "Hi Sam! Your next automatic payment for Ticketmaster is coming up.", "2026-11-13")
BNPL_FAILED = msg("k2", "Klarna", "Payment failed for Ticketmaster",
                  "Your payment of $10.44 didn't go through. Pay now to avoid a late fee.", "2026-11-15")
BNPL_PAID = msg("k0", "Klarna", "2nd payment received for Ticketmaster",
                "Your payment of $10.44 was successful. Thanks for your payment Hi Sam! Your next "
                "payment of $10.44 will be charged on November 14, 2026.", "2026-10-31")
DEPOSIT = msg("c1", "Capital One | Secured Card",
              "Reminder: Complete your deposit to access your new card",
              "Fund your new card before your application expires. Make your $200 deposit to get the "
              "card you were approved for. Remaining amount due $200 Due date December 18, 2026 "
              "Starting credit line $200", "2026-11-16")
ADVANCE_1 = msg("d1", "Dave", "Get ready for cash",
                "Your $40 ExtraCash is on the way. Your $40 has landed in your Dave Checking account. "
                "Transfer $40 Arrived instantly OD Service Fee $5 Settlement amount $45 Settles on "
                "Fri, Oct 9, 2026", "2026-10-06T09:33:21Z")
ADVANCE_2 = msg("d2", "Dave", "Get ready for cash",
                "Your $35 ExtraCash is on the way. Transfer $35 Arrived instantly OD Service Fee $5 "
                "Settlement amount $85 Settles on Fri, Oct 9, 2026", "2026-10-06T18:04:11Z")
SETTLING = msg("d3", "Dave", "Your ExtraCash settlement is processing",
               "Hi Sam, Your $5 ExtraCash settlement is processing through ACH.", "2026-10-09")
LOAN = msg("l1", "Chime", "You got an Instant Loan",
           "Your loan confirmation Hi Sam, Congrats on your $100 loan! That money is already in "
           "your Checking Account.", "2026-10-29")
SPOTME = msg("l2", "Chime", "You were covered by SpotMe and paid it back.",
             "Great news! Your recent deposit just repaid the negative balance on your account",
             "2026-11-13")
MEMBERSHIP = msg("s1", "FloatMe Support", "Membership Fee Withdrawal Reminder",
                 "Hi Sam, Your monthly membership fee of $6.99 will be withdrawn from your account "
                 "ending in 1234 on 11/16/2026.", "2026-11-12")
RENEWAL = msg("s2", "IPRoyal", "Upcoming Renewal Reminder",
              "This is a friendly reminder that we will charge your order #12345678 subscription "
              "in 5 days, on November 18, 2026. We charge 3 days before your renewal date.", "2026-11-13")
DOMAIN = msg("s3", "Spaceship", "Upcoming payment: Your example.dev will be renewed in 30 days",
             "Sit back and relax as you have auto-renewal enabled.", "2026-11-16")
STORE = msg("s4", "Apple", "Your receipt from Apple.",
            "Your Apple Account was used to purchase 100 GB. Receipt Save 3% on all your purchases",
            "2026-11-07")
DOMAIN_FAILED = msg("f1", "Cloudflare Registrar", "Domain Auto-Renewals Failed",
                    "Auto-renewal failed for 1 domain example.com, expires October 31, 2026 Payment "
                    "failed. Renew manually or update your billing details.", "2026-10-30")
CLOUD_DECLINED = msg("g1", "Google Payments", "Google Cloud Platform & APIs: Payment was declined",
                     "Your financial institution declined a payment from your Mastercard", "2026-11-02")
CLOUD_PAID = msg("g2", "Google Payments", "Google: We've received your payment for 0000-1111",
                 "Payment received Your payment amount of $12.17 to Google was received on "
                 "Nov 9, 2026.", "2026-11-09")
NOISE = [
    msg("n1", "WalletHub", "Credit Report Update: Loan Payment Status",
        "we monitor for any missed payments. LAST 31 DAYS: No past-due payments detected", "2026-11-09"),
    msg("n2", "Klarna", "Klarna Days is here! Double cashback.", "Shop top stores", "2026-11-13",
        labels=["CATEGORY_PROMOTIONS"]),
    msg("n3", "Dave", "You have up to $35 ExtraCash waiting", "Request the rest today", "2026-10-06"),
    msg("n4", "A Relative", "Fwd: Your Ring Subscription Payment Was Unsuccessful",
        "Forwarded message", "2026-11-11", addr="someone@gmail.com"),
    msg("n5", "Guitar Center", "Your order is ready", "Order Number: GC1 Thanks for shopping",
        "2026-11-13"),
    msg("n6", "Dave", "Your account statement(s) for October 2026",
        "Your monthly account statement(s) is available in the app.", "2026-11-15"),
]


def kinds(message):
    return [(f["type"], f["amount"], f["due"]) for f in mailscan.classify(message)]


class Classify(unittest.TestCase):
    def test_upcoming_failed_and_paid_instalments(self):
        self.assertEqual(kinds(BNPL_UPCOMING), [("due", 10.44, "2026-11-14")])
        self.assertEqual(kinds(BNPL_FAILED), [("failed", 10.44, None)])
        self.assertEqual(kinds(BNPL_PAID), [("paid", 10.44, None), ("due", 10.44, "2026-11-14")])
        self.assertEqual(mailscan.classify(BNPL_FAILED)[0]["name"], "Klarna: Ticketmaster")

    def test_deposit_deadline_uses_clean_sender_name(self):
        found = mailscan.classify(DEPOSIT)[0]
        self.assertEqual((found["type"], found["amount"], found["due"], found["name"]),
                         ("due", 200.0, "2026-12-18", "Capital One"))

    def test_cash_advance_uses_settlement_amount_and_date(self):
        self.assertEqual(kinds(ADVANCE_2), [("advance", 85.0, "2026-10-09")])
        self.assertEqual(kinds(LOAN), [("advance", 100.0, None)])
        self.assertEqual(kinds(SETTLING), [("paid", 5.0, None)])

    def test_subscriptions(self):
        self.assertEqual(kinds(MEMBERSHIP), [("sub", 6.99, "2026-11-16")])
        self.assertEqual(kinds(RENEWAL), [("sub", None, "2026-11-18")])
        self.assertEqual(kinds(DOMAIN), [("sub", None, "2026-12-16")])
        self.assertEqual(mailscan.classify(DOMAIN)[0]["name"], "Spaceship: example.dev")
        self.assertEqual(mailscan.classify(STORE)[0]["name"], "Apple: 100 GB")

    def test_failed_renewal_and_declined_card(self):
        self.assertEqual(kinds(DOMAIN_FAILED), [("failed", None, "2026-10-31")])
        self.assertEqual(kinds(CLOUD_DECLINED), [("failed", None, None)])

    def test_noise_produces_nothing(self):
        for message in NOISE:
            self.assertEqual(mailscan.classify(message), [], message["subject"])


class Apply(unittest.TestCase):
    def scan(self, ledger, *messages):
        return mailscan.scan_messages(ledger, list(messages))

    def test_instalment_lifecycle_ends_overdue_once(self):
        ledger = {"obligations": []}
        self.scan(ledger, BNPL_FAILED, BNPL_PAID, BNPL_UPCOMING)  # arrival order must not matter
        self.assertEqual(len(ledger["obligations"]), 1)
        ob = ledger["obligations"][0]
        self.assertEqual((ob["name"], ob["amount"], ob["due"], ob["payments"]),
                         ("Klarna: Ticketmaster", 10.44, "2026-11-14", []))
        self.assertIn("Payment failed", ob["note"])

    def test_rescan_changes_nothing(self):
        ledger = {"obligations": []}
        first = self.scan(ledger, ADVANCE_1, ADVANCE_2, SETTLING, MEMBERSHIP)
        snapshot = (len(ledger["obligations"]), len(ledger["subs"]),
                    ledger["obligations"][0]["payments"])
        again = self.scan(ledger, ADVANCE_1, ADVANCE_2, SETTLING, MEMBERSHIP)
        self.assertEqual(first["new"], 4)
        self.assertEqual(again["new"], 0)
        self.assertEqual(snapshot, (1, 1, [{"date": "2026-10-09", "amount": 5.0}]))
        self.assertEqual(ledger["obligations"][0]["amount"], 85.0)
        self.assertFalse(ledger["obligations"][0]["verified"])

    def test_adopts_a_row_typed_by_hand_instead_of_duplicating(self):
        ledger = {"obligations": [{"id": "a", "name": "Chime Instant Loan", "amount": 100,
                                   "due": None, "payments": [], "verified": False, "note": "mine"}]}
        self.scan(ledger, LOAN, SPOTME)
        self.assertEqual(len(ledger["obligations"]), 1)
        self.assertEqual(ledger["obligations"][0]["payments"], [])  # vague "paid it back" ignored

    def test_payment_closes_an_open_failure_with_unknown_amount(self):
        ledger = {"obligations": []}
        self.scan(ledger, CLOUD_DECLINED, CLOUD_PAID)
        ob = ledger["obligations"][0]
        self.assertEqual((ob["amount"], ob["payments"]),
                         (12.17, [{"date": "2026-11-09", "amount": 12.17}]))

    def test_deleted_row_stays_deleted_until_new_mail_arrives(self):
        ledger = {"obligations": []}
        self.scan(ledger, DOMAIN_FAILED)
        ledger["obligations"].clear()
        self.scan(ledger, DOMAIN_FAILED)
        self.assertEqual(ledger["obligations"], [])

    def test_subscription_tracks_charges(self):
        ledger = {"obligations": []}
        later = dict(STORE, id="s5", date="2026-12-07")
        self.scan(ledger, STORE, later)
        sub = ledger["subs"][0]
        self.assertEqual((sub["name"], sub["charges"]), ("Apple: 100 GB", ["2026-11-07", "2026-12-07"]))


class Imap(unittest.TestCase):
    def test_raw_email_becomes_a_message(self):
        raw = EmailMessage()
        raw["From"] = '"FloatMe Support" <support@mail.floatme.com>'
        raw["Subject"] = "Membership Fee Withdrawal Reminder"
        raw["Date"] = "Thu, 12 Nov 2026 13:19:20 +0000"
        raw.set_content("ignored plain part")
        raw.add_alternative("<html><style>p{}</style><p>Your monthly membership fee of "
                            "<b>$6.99</b> will be withdrawn on 11/16/2026.</p></html>", subtype="html")
        raw.get_payload()[0].set_payload("")  # some senders ship an empty text part
        message = mailscan.message_from_bytes(bytes(raw), 0x1a1cfe9bbfac36da)
        self.assertEqual(message["id"], "1a1cfe9bbfac36da")
        self.assertEqual(message["from_name"], "FloatMe Support")
        self.assertEqual(message["date"], "2026-11-12")
        self.assertEqual(kinds(message), [("sub", 6.99, "2026-11-16")])


if __name__ == "__main__":
    unittest.main()
