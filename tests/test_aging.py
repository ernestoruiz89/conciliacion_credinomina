import unittest

from credinomina_reconciliation.aging import age_balance, collection_shortfall_usd, operational_balances


class AgingTests(unittest.TestCase):
    def test_invalid_detail_never_confirms_employee_or_employer_debt(self):
        from credinomina_reconciliation.reconciliation import classify_deduction
        amounts = dict(expected_usd=100, expected_nio=3650, deducted_usd=50, deducted_nio=3650)
        self.assertEqual(classify_deduction(**amounts), "Importes inconsistentes")
        for status in ("Importes inconsistentes", "Moneda no coincide", "Importe invalido", "Estado desconocido"):
            with self.subTest(status=status):
                row = {**amounts, "deduction_status": status}
                self.assertEqual(collection_shortfall_usd(row), 100)
                balances = list(operational_balances(row, {}))
                self.assertEqual(len(balances), 1)
                self.assertEqual(balances[0]["balance_type"], "Diferencia cobranza vs aplicación (informativo)")
                self.assertEqual(balances[0]["amount_usd"], 100)
                self.assertEqual(balances[0]["provision_review_usd"], 0)

    def test_age_bands_are_exclusive_at_boundaries(self):
        cases = [
            ("2026-10-10", "not_due", 0),
            ("2026-10-11", "days_1_15", 1),
            ("2026-10-25", "days_1_15", 15),
            ("2026-10-26", "days_16_30", 16),
            ("2026-11-09", "days_16_30", 30),
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
                    "not_due", "days_1_15", "days_16_30", "days_31_60",
                    "days_61_90", "days_over_90", "without_date",
                )), 23.5)

    def test_missing_due_date_is_not_treated_as_current(self):
        result = age_balance(50, None, "2026-12-31")
        self.assertIsNone(result["age_days"])
        self.assertEqual(result["without_date"], 50)

    def test_worker_employer_and_unconfirmed_detail_are_separate(self):
        row = dict(expected_usd=60, applied_usd=30, deducted_usd=50, remitted_usd=10)
        result = list(operational_balances(row, {}))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["amount_usd"], 30)
        self.assertEqual(result[0]["provision_review_usd"], 0)
        self.assertEqual(result[0]["balance_type"], "Diferencia cobranza vs aplicación (informativo)")

    def test_employee_receivable_requires_company_detail(self):
        for status in (None, "Pendiente de detalle", "Deduccion parcial"):
            self.assertEqual(collection_shortfall_usd(dict(expected_usd=46.52, applied_usd=20,
                deducted_usd=46.52, deduction_status=status)), 26.52)

    def test_fx_and_negative_rounding_do_not_inflate_company_balance(self):
        rows = list(operational_balances({
            "expected_usd": 50, "applied_usd": 50, "deducted_usd": 50, "remitted_usd": 49.98,
            "fx_variance_usd": 0.01, "rounding_adjustment_usd": -0.01,
            "deduction_status": "Deduccion total",
        }, {"cutoff_date": "2026-09-30", "remittance_due_date": "2026-10-10"}))
        self.assertEqual(rows, [])
