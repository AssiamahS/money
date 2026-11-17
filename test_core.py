import unittest
from datetime import date

import core

TODAY = date(2026, 11, 17)

APPLE_CSV = """Transaction Date,Clearing Date,Description,Merchant,Category,Type,Amount (USD),Purchased By
11/10/2026,11/11/2026,"UBER *EATS HELP.UBER.COM","Uber Eats","Restaurants","Purchase","23.50","Me"
11/09/2026,11/10/2026,"ACH DEPOSIT INTERNET TRANSFER","Ach Deposit","Payment","Payment","-50.00","Me"
11/08/2026,11/09/2026,"WAWA 8012","Wawa","Gas","Purchase","31.07","Me"
"""

GENERIC_CSV = """Date,Description,Amount
2026-11-01,DUNKIN #3312,-4.89
2026-11-02,PAYROLL VICTRA,612.40
"""


class Obligations(unittest.TestCase):
    def test_goal_with_partial_payments(self):
        ob = {"amount": 200, "due": "2026-12-18",
              "payments": [{"date": "2026-11-17", "amount": 45}]}
        v = core.obligation_view(ob, TODAY)
        self.assertEqual(v["days_left"], 31)
        self.assertEqual(v["remaining"], 155)
        self.assertEqual(v["per_day"], 5.0)
        self.assertEqual(v["per_week"], 31.0)  # 155 over 5 remaining weeks
        self.assertEqual(v["status"], "ok")

    def test_overdue_and_paid(self):
        late = core.obligation_view({"amount": 10.44, "due": "2026-11-14"}, TODAY)
        self.assertEqual(late["status"], "overdue")
        self.assertEqual(late["days_left"], -3)
        paid = core.obligation_view(
            {"amount": 10.44, "due": "2026-11-14",
             "payments": [{"date": "2026-11-17", "amount": 10.44}]}, TODAY)
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(paid["remaining"], 0)

    def test_unknown_amount_or_date_does_not_crash(self):
        v = core.obligation_view({"amount": None, "due": None}, TODAY)
        self.assertEqual(v["status"], "unknown")
        self.assertIsNone(v["remaining"])
        v = core.obligation_view({"amount": 100, "due": None}, TODAY)
        self.assertEqual(v["status"], "no_date")
        self.assertEqual(v["remaining"], 100)

    def test_due_today_needs_all_of_it(self):
        v = core.obligation_view({"amount": 60, "due": "2026-11-17"}, TODAY)
        self.assertEqual(v["days_left"], 0)
        self.assertEqual(v["per_day"], 60)
        self.assertEqual(v["status"], "soon")


class CsvImport(unittest.TestCase):
    def test_apple_card_export(self):
        rows = core.parse_csv(APPLE_CSV, "Apple Card")
        self.assertEqual(len(rows), 3)
        eats = rows[0]
        self.assertEqual(eats["date"], "2026-11-10")
        self.assertEqual(eats["merchant"], "Uber Eats")
        self.assertEqual(eats["category"], "Restaurants")
        self.assertEqual(eats["amount"], 23.5)
        self.assertEqual(eats["kind"], "spend")
        self.assertEqual(rows[1]["kind"], "payment")

    def test_generic_bank_csv_negative_is_spend(self):
        rows = core.parse_csv(GENERIC_CSV, "Chime")
        self.assertEqual(rows[0]["kind"], "spend")
        self.assertEqual(rows[0]["amount"], 4.89)
        self.assertEqual(rows[0]["category"], "Coffee & food")
        self.assertEqual(rows[1]["kind"], "income")
        self.assertEqual(rows[1]["amount"], 612.4)

    def test_reimport_does_not_duplicate(self):
        first = core.parse_csv(APPLE_CSV, "Apple Card")
        merged, added = core.merge_txns([], first)
        self.assertEqual(added, 3)
        merged, added = core.merge_txns(merged, core.parse_csv(APPLE_CSV, "Apple Card"))
        self.assertEqual(added, 0)
        self.assertEqual(len(merged), 3)

    def test_unreadable_csv_raises(self):
        with self.assertRaises(ValueError):
            core.parse_csv("foo,bar\n1,2\n", "x")


class Summary(unittest.TestCase):
    def test_spend_only_totals_by_category_and_month(self):
        rows = core.parse_csv(APPLE_CSV, "Apple Card") + core.parse_csv(GENERIC_CSV, "Chime")
        s = core.summarize(rows, "2026-11")
        self.assertEqual(s["spent"], 59.46)
        self.assertEqual(s["income"], 612.4)
        self.assertEqual(s["by_category"][0], {"name": "Gas", "amount": 31.07})
        self.assertEqual(s["months"], ["2026-11"])


if __name__ == "__main__":
    unittest.main()
