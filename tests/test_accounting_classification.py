import csv
import io
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.accounting_types import APPLICATION, DEBIT_NOTE, INTERNAL, REVIEW, classify_movement
from credinomina_reconciliation import accounting_review as review
from credinomina_reconciliation.parsers import parse_accounting_movements, apply_accounting_currency_override


def csv_rows(*rows):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "TMOV", "TDOC", "NO_CMPTE", "NO_REF", "DESCRIPCION", "DEBITO_DEL_MES", "CREDITO_DEL_MES", "Clasificacion"])
    writer.writerows(rows)
    return stream.getvalue().encode()


class ClassificationTests(unittest.TestCase):
    def test_confirmed_codes_normalize_excel_numbers(self):
        for tmov, tdoc in [(12, 5), ("12.0", "19.0"), ("12", "06")]:
            kind, payment, _ = classify_movement(tmov, tdoc, 100, 0)
            self.assertEqual((kind, payment), (APPLICATION, True))
        self.assertEqual(classify_movement(12, 16, 100, 0)[:2], (DEBIT_NOTE, False))
        for pair in [(1, 1), (12, 1), (5, 1)]:
            self.assertEqual(classify_movement(*pair, 100, 0)[:2], (INTERNAL, False))

    def test_reverse_or_credit_is_never_a_positive_payment(self):
        for debit, credit, text in [(0, 100, ""), (100, 20, ""), (-10, 0, ""), (100, 0, "REVERSIÓN DE PAGO")]:
            self.assertFalse(classify_movement(12, 6, debit, credit, text)[1])
        self.assertEqual(classify_movement(99, 99, 10, 0)[:2], (REVIEW, False))
        self.assertFalse(classify_movement("", "", 10, 0, "NOTA AL PRESTAMO PAGO APLICADO")[1])

    def test_parser_preserves_internal_credit_unknown_without_loan(self):
        rows = parse_accounting_movements("test.csv", csv_rows(
            ["1602", "2025-04-15", 12, 5, "001", "REF", "PAGO", 366.24, 0, ""],
            ["1602", "2025-04-15", 12, 1, "002", "NO ES DEPOSITO", "NOTA AL PRESTAMO 123-1", 100, 0, "Manual"],
            ["1602", "2025-04-15", 12, 16, "003", "texto", "ND", 0, 100, ""],
            ["1602", "2025-04-15", 99, 99, "004", "", "", 46.53, 46.52, ""],
            ["TOTAL", "", "", "", "", "", "TOTAL", 1000, 1000, ""],
        ))
        self.assertEqual([r["event_type"] for r in rows], ["Aplicacion", "Ajuste", "Ajuste", "Ajuste"])
        self.assertEqual(rows[1]["source_classification"], "Manual")
        self.assertEqual(rows[1]["accounting_reference"], "NO ES DEPOSITO")
        self.assertEqual(rows[2]["loan_number"], "")
        self.assertEqual(rows[3]["amount"], 0.01)
        apply_accounting_currency_override(rows, "NIO", "36.6243")
        self.assertEqual(rows[0]["amount_usd"], 10)
        self.assertEqual(rows[2]["source_credit"], 100)
        self.assertEqual(rows[2]["source_currency"], "NIO")

    def test_unknown_company_is_not_forced_to_default(self):
        employers = [{"name": "A", "employer_name": "Empresa A", "employer_code": "A"}]
        rows = review.plan_review_items([
            {"event_type": "Ajuste", "employer_text": "No registrada"},
            {"event_type": "Ajuste", "employer_text": "A"},
            {"event_type": "Ajuste", "employer_text": ""},
            {"event_type": "Ajuste", "employer_text": "A", "portfolio_employer": "B"},
        ], employers, "A")
        self.assertEqual([row["resolved_employer"] for row in rows], ["NO IDENTIFICADA", "A", "A", ""])


class ReviewGuardTests(unittest.TestCase):
    def setUp(self):
        self.translation = patch.object(review, "_", side_effect=lambda s: s)
        self.translation.start()
        self.throw = patch.object(review.frappe, "throw", side_effect=ValueError)
        self.throw.start()
        self.addCleanup(self.translation.stop)
        self.addCleanup(self.throw.stop)

    def doc(self, **values):
        return frappe._dict({"accounting_source_key": "source", "category": "Por clasificar", "docstatus": 0,
                             "accounting_classification": INTERNAL, "review_action": "Pendiente de revisión", **values})

    def test_draft_can_lack_company_reference_or_credit(self):
        doc = self.doc()
        review.validate_review_item(doc)
        self.assertEqual(doc.review_status, "Pendiente de identificar")

    def test_drafts_never_submit_without_review(self):
        for action in ["Pendiente de revisión", "No conciliatoria", "Reversión identificada"]:
            with self.subTest(action=action), self.assertRaises(ValueError):
                review.validate_review_item(self.doc(docstatus=1, review_action=action, review_notes="revisado"))

    def test_deposit_requires_explicit_review_and_company(self):
        values = dict(docstatus=1, review_action="Partida de depósito", review_notes="Cobranza administrativa",
                      category="Cobranza administrativa", employer="A", reference="DEP", amount_reviewed=1)
        doc = self.doc(**values)
        review.validate_review_item(doc)
        self.assertEqual(doc.review_status, "Lista para conciliar")
        for field in ["employer", "reference", "amount_reviewed", "review_notes"]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                review.validate_review_item(self.doc(**{**values, field: ""}))

    def test_reversals_cannot_be_deposit_complements(self):
        for kind in [APPLICATION, DEBIT_NOTE]:
            with self.assertRaises(ValueError):
                review.validate_review_item(self.doc(accounting_classification=kind, review_action="Partida de depósito", review_notes="revisada"))

    def test_evidence_is_immutable(self):
        original = self.doc(source_credit=100)
        with self.assertRaises(ValueError):
            review.validate_review_item(self.doc(source_credit=90), original)

    def test_application_link_must_belong_to_company(self):
        with patch.object(review.frappe, "db", Mock(get_value=Mock(return_value=frappe._dict(
            parent="IMPORT", parenttype="CN Accounting Import", event_type="Aplicacion", effective=1)))), patch.object(
            review.frappe, "get_doc", return_value=frappe._dict(employer="B", check_permission=Mock())):
            with self.assertRaises(ValueError):
                review.validate_review_item(self.doc(employer="A", related_application="ROW"))


if __name__ == "__main__":
    unittest.main()
