import unittest

from credinomina_reconciliation.deduction_recognition import recognition_reason


class DepositRecognitionTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"expected_usd": 40, "expected_nio": 1480, "deduction_status": "Pendiente de detalle"},
            {"expected_usd": 60, "expected_nio": 2220, "deduction_status": "Pendiente de detalle"},
        ]
        self.account = {"reference": "R-1", "currency": "USD", "amount": 100}
        self.bank = dict(self.account)

    def test_exact_usd_deposit_can_support_full_collection(self):
        self.assertEqual("", recognition_reason(self.rows, self.account, self.bank))

    def test_partial_or_reused_deposit_cannot_confirm_every_employee(self):
        self.bank["amount"] = 90
        self.assertTrue(recognition_reason(self.rows, self.account, self.bank))
        self.bank["amount"] = 100
        self.account["allocated_usd"] = 10
        self.assertIn("distribución", recognition_reason(self.rows, self.account, self.bank))

    def test_nio_payment_requires_documented_rate_and_nio_match(self):
        self.account = {
            "reference": "R-1", "currency": "NIO", "amount": 3700,
            "equivalent_currency": "USD", "equivalent_amount": 100,
            "fx_basis": "Comprobante bancario",
        }
        self.bank = dict(self.account)
        self.assertEqual("", recognition_reason(self.rows, self.account, self.bank))
        self.account["amount"] = self.bank["amount"] = 3690
        self.assertIn("C$", recognition_reason(self.rows, self.account, self.bank))
        self.account["fx_basis"] = self.bank["fx_basis"] = ""
        self.assertIn("tipo de cambio", recognition_reason(self.rows, self.account, self.bank))

    def test_deduction_already_recorded_cannot_be_overwritten(self):
        self.rows[0]["deduction_status"] = "Deduccion parcial"
        self.assertIn("ya tiene", recognition_reason(self.rows, self.account, self.bank))


if __name__ == "__main__":
    unittest.main()
