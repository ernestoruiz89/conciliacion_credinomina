import unittest

from credinomina_reconciliation.accounting_assignments import apply_assignments
from credinomina_reconciliation.accounting_batch import group_applications, accounting_group_csv
from credinomina_reconciliation.parsers import parse_accounting_movements, SourceFileError


class AccountingAssignmentTests(unittest.TestCase):
    employers = [{"name": "A"}, {"name": "B"}]

    def rows(self):
        return [{"source_row": index, "event_date": "2025-04-15", "event_type": "Aplicacion",
                 "employer_text": "Sin alias", "amount_usd": 10, "amount": 10, "currency": "USD"}
                for index in (2, 3)]

    def test_assignments_regroup_without_modifying_source_text(self):
        rows = apply_assignments(self.rows(), self.employers, {"2": "A", "3": "A"})
        groups = group_applications(rows, self.employers)["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["employer"], "A")
        self.assertEqual(groups[0]["count"], 2)
        self.assertTrue(all(row["employer_text"] == "Sin alias" for row in rows))

    def test_manual_choice_over_default_for_blank_text(self):
        rows = self.rows()[:1]
        rows[0]["employer_text"] = ""
        apply_assignments(rows, self.employers, {"2": "B"})
        plan = group_applications(rows, self.employers, "A")
        self.assertEqual(plan["issues"], [])
        self.assertEqual(plan["groups"][0]["employer"], "B")

    def test_rejects_nonexistent_row_unreadable_company_and_conflict(self):
        for mapping in ({"99": "A"}, {"2": "Missing"}, {"2": ["A"]}, [], {"2": "NO IDENTIFICADA"}):
            with self.subTest(mapping=mapping), self.assertRaises(SourceFileError):
                apply_assignments(self.rows(), self.employers, mapping)
        for evidence in ({"portfolio_employer": "B"}, {"employer_text": "B"},
                         {"portfolio_validation_status": "Crédito duplicado en corte"}):
            with self.subTest(evidence=evidence), self.assertRaises(SourceFileError):
                apply_assignments([{**self.rows()[0], **evidence}], self.employers, {"2": "A"})

    def test_csv_retains_manual_decision_separately_from_original(self):
        original = {2: {"cuenta_contable": "1602", "fecha_aplica": "2025-04-15", "tmov": "12", "tdoc": "05",
                       "descripcion": "Asiento original", "empresa": "Sin alias", "debito_del_mes": 10, "credito_del_mes": 0}}
        rows = apply_assignments(self.rows()[:1], self.employers, {"2": "A"})
        parsed = parse_accounting_movements("test.csv", accounting_group_csv(original, rows))
        self.assertEqual(parsed[0]["_csv_employer_assignment"], "A")
        self.assertEqual(parsed[0]["employer_text"], "Sin alias")
        self.assertEqual(parsed[0]["source_description"], "Asiento original")
