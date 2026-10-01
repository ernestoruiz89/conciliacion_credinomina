import unittest

from credinomina_reconciliation.bulk_accounting_import import _summary


class BulkIdentificationSummaryTests(unittest.TestCase):
    def plan(self):
        unknown = [{"source_row": i + 2, "event_date": "2025-04-15", "amount_usd": 0.01,
                    "client_name": "Cliente", "employer_text": "Texto original", "loan_number": "123-1",
                    "voucher": "AS-1", "portfolio_validation_status": "Crédito no encontrado"} for i in range(105)]
        identified = [{"source_row": 200, "amount_usd": 12.34}]
        return {"groups": [
            {"employer": "Empresa A", "event_date": "2025-04-15", "count": 1, "total_usd": 12.34, "rows": identified},
            *[{"employer": "NO IDENTIFICADA", "event_date": "2025-04-15", "count": 1, "total_usd": .01, "rows": [row]} for row in unknown]],
            "complementary": [{"source_row": i + 300, "resolved_employer": company,
                "accounting_classification": "Movimiento interno", "classification_reason": "Revisar", "employer_text": "Origen"}
                for i, company in enumerate(["Empresa A"] * 105 + ["NO IDENTIFICADA"])],
            "deposits": [{"source_row": i + 500, "resolved_employer": company, "deposit_currency": "USD",
                "deposit_amount": 5, "bank_deposit_reference": "DEP", "employer_text": "Original"}
                for i, company in enumerate(["Empresa A"] * 105 + ["NO IDENTIFICADA"])],
            "issues": [], "duplicates": [], "excluded": [], "already_imported": []}

    def test_separate_counts_and_exact_totals_before_preview_truncation(self):
        summary = _summary(self.plan())
        known, unknown = summary["sections"]["identified"], summary["sections"]["unidentified"]
        self.assertEqual(known["rows"], 1)
        self.assertEqual(known["application_total_usd"], 12.34)
        self.assertEqual(unknown["rows"], 105)
        self.assertEqual(unknown["application_total_usd"], 1.05)
        self.assertEqual(len(unknown["applications"]), 100)
        self.assertEqual(unknown["applications"][0]["employer_text"], "Texto original")
        self.assertEqual(unknown["applications"][0]["voucher"], "AS-1")
        self.assertEqual(len(known["complementary"]), 100)
        self.assertEqual(known["complementary_count"], 105)
        self.assertEqual(unknown["complementary_count"], 1)
        self.assertEqual(len(unknown["complementary"]), 1)
        self.assertEqual(unknown["deposit_count"], 1)
        self.assertEqual(len(unknown["deposits"]), 1)
        self.assertEqual(summary["rows"], known["rows"] + unknown["rows"])

    def test_empty_sections_are_explicit(self):
        plan = {key: [] for key in ("groups", "complementary", "deposits", "issues", "duplicates", "excluded", "already_imported")}
        for section in _summary(plan)["sections"].values():
            self.assertEqual(section["rows"], 0)
            self.assertEqual(section["application_total_usd"], 0)
            self.assertEqual(section["groups"], [])
