import unittest

import frappe

from credinomina_reconciliation.reclassification_deposit import credit_origin_date, reconciliation_date, validate_pair


class ReclassificationDepositValidationTests(unittest.TestCase):
    def setUp(self):
        self.item = dict(docstatus=1, category="Compensación entre partidas", accounting_source_key="CORE",
            source_debit=704.89, source_credit=0, employer="MULTIPERFILES", client_name="DELVIN FRANCISCO GUTIERREZ BOBADILLA",
            loan_number="109586-1", amount_usd=19.25)
        self.deposit = dict(name="DEP-8-2025-0010", docstatus=1)
        self.row = dict(parent=self.deposit["name"], employer="MULTIPERFILES", client="5470", client_number="5470",
            client_name="DELVIN\u00a0FRANCISCO\u00a0GUTIERREZ\u00a0BOBADILLA", loan_number="00109586-1", amount_usd=19.25)

    def test_matching_debit_and_detail(self):
        validate_pair(self.item, self.deposit, self.row)

    def test_effective_date_is_later_of_deposit_and_reclassification(self):
        item = dict(self.item, posting_date="2025-11-21")
        deposit = dict(self.deposit, deposit_date="2025-08-07")
        self.assertEqual(str(credit_origin_date(deposit)), "2025-08-07")
        self.assertEqual(str(reconciliation_date(item, deposit)), "2025-11-21")
        deposit["deposit_date"] = "2025-12-01"
        self.assertEqual(str(credit_origin_date(deposit)), "2025-12-01")
        self.assertEqual(str(reconciliation_date(item, deposit)), "2025-12-01")

    def test_rejects_wrong_direction_and_contradictory_evidence(self):
        for field, value in [("source_debit", 0), ("source_credit", 1), ("employer", "OTRA"),
                             ("client_name", "OTRA PERSONA"), ("loan_number", "109586-2"),
                             ("amount_usd", 19.26), ("registered_deposit", "OTRO")]:
            with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
                validate_pair(dict(self.item, **{field: value}), self.deposit, self.row)
