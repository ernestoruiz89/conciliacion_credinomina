import unittest

from credinomina_reconciliation.aging import age_balance, employee_receivable_usd, operational_balances


class AgingTests(unittest.TestCase):
    def test_age_bands_are_exclusive_at_boundaries(self):
        cases = [
            ("2026-10-10", "not_due", 0),
            ("2026-10-11", "days_1_30", 1),
            ("2026-11-09", "days_1_30", 30),
            ("2026-11-10", "days_31_60", 31),
            ("2026-12-09", "days_31_60", 60),
            ("2026-12-10", "days_61_90", 61),
            ("2027-01-08", "days_61_90", 90),
            ("2027-01-09", "days_over_90", 91),
        ]
        for as_of, expected, days in cases:
            with self.subTest(as_of=as_of):
                result = age_balance(23.5, "2026-10-10", as_of)
                self.assertEqual(result["age_bucket"], expected)
                self.assertEqual(result["age_days"], days)
                self.assertEqual(sum(result[name] for name in (
                    "not_due", "days_1_30", "days_31_60",
                    "days_61_90", "days_over_90", "without_date",
                )), 23.5)

    def test_missing_due_date_is_not_treated_as_current(self):
        result = age_balance(50, None, "2026-12-31")
        self.assertIsNone(result["age_days"])
        self.assertEqual(result["without_date"], 50)

    def test_worker_employer_and_unconfirmed_detail_are_separate(self):
        period = {"cutoff_date": "2026-09-30", "remittance_due_date": "2026-10-10"}
        rows = list(operational_balances({
            "expected_usd": 60, "deducted_usd": 30, "remitted_usd": 10,
            "deduction_status": "Deduccion parcial",
        }, period))
        self.assertEqual([row["amount_usd"] for row in rows], [30, 20])
        self.assertEqual(rows[0]["balance_type"], "CxC a empleados (cuota no deducida)")
        self.assertEqual(rows[1]["balance_type"], "Deducido sin remesa asignada")
        self.assertEqual([row["due_date"] for row in rows], ["2026-09-30", "2026-10-10"])
        self.assertEqual([row["provision_review_usd"] for row in rows], [30, 0])
        unknown = list(operational_balances({
            "expected_usd": 60, "deducted_usd": 0,
            "deduction_status": "Pendiente de detalle",
        }, period))
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["balance_type"], "Detalle de empresa pendiente")
        self.assertEqual(unknown[0]["provision_review_usd"], 0)

    def test_employee_receivable_requires_company_detail(self):
        self.assertIsNone(employee_receivable_usd({
            "expected_usd": 46.52, "deducted_usd": 0,
            "deduction_status": "Pendiente de detalle",
        }))
        self.assertIsNone(employee_receivable_usd({
            "expected_usd": 46.52, "deducted_usd": 0,
        }))
        self.assertEqual(employee_receivable_usd({
            "expected_usd": 46.52, "deducted_usd": 20,
            "deduction_status": "Deduccion parcial",
        }), 26.52)
        self.assertEqual(employee_receivable_usd({
            "expected_usd": 46.52, "deducted_usd": 46.53,
            "deduction_status": "Deduccion en exceso",
        }), 0)

    def test_fx_and_negative_rounding_do_not_inflate_company_balance(self):
        rows = list(operational_balances({
            "expected_usd": 50, "deducted_usd": 50, "remitted_usd": 49.98,
            "fx_variance_usd": 0.01, "rounding_adjustment_usd": -0.01,
            "deduction_status": "Deduccion total",
        }, {"cutoff_date": "2026-09-30", "remittance_due_date": "2026-10-10"}))
        self.assertEqual(rows, [])
