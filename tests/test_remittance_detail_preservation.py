import copy
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.remittance_detail_preservation import preserved_rows, validate_application_rows
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as controller


class DetailPreservationTests(unittest.TestCase):
    def case(self):
        rows = [frappe._dict(name=name, source_row=index, client_number=client, client_name=client,
            employer="EMP", loan_number="00123-1", deducted_usd=amount, amount_usd=amount,
            pending_usd=amount, matched_targets="[]", difference_reason="Manual", difference_notes="Conservar")
            for index, (name, client, amount) in enumerate((("A", "C1", 294.32), ("B", "C2", 20)), 2)]
        targets = [frappe._dict(name=str(index), detail_row="A", historical_application=app, amount_usd=147.16)
                   for index, app in enumerate(("APP1", "APP2"))]
        targets.append(frappe._dict(detail_row="B", complementary_item="ITEM", amount_usd=20))
        document = frappe._dict(detail_rows=rows, targets=targets)
        candidates = [{**row, "name": None, "difference_reason": None, "difference_notes": None} for row in rows]
        return document, candidates

    def test_reordered_rows_preserve_names_multiple_targets_and_manual_documentation(self):
        doc, candidates = self.case()
        before = copy.deepcopy(doc)
        candidates.reverse()
        candidates[0].update(source_row=2, comments="nuevo")
        candidates[1].update(source_row=3, loan_number="123-1")
        result = preserved_rows(doc, candidates)
        self.assertEqual([row["name"] for row in result], ["B", "A"])
        self.assertEqual(result[0]["comments"], "nuevo")
        self.assertEqual(result[1]["loan_number"], "00123-1")
        self.assertEqual(result[1]["difference_notes"], "Conservar")
        self.assertEqual(doc, before)

    def test_missing_changed_split_and_ambiguous_linked_rows_abort_without_mutation(self):
        for change in ("missing", "amount", "client", "loan", "duplicate", "split", "orphan"):
            with self.subTest(change=change):
                doc, candidates = self.case()
                if change == "missing": candidates.pop(0)
                if change == "amount": candidates[0]["amount_usd"] = 294.31
                if change == "client": candidates[0]["client_number"] = "OTHER"
                if change == "loan": candidates[0]["loan_number"] = "OTHER"
                if change == "duplicate": candidates.append(dict(candidates[0]))
                if change == "split":
                    candidates[0].update(amount_usd=147.16, deducted_usd=147.16)
                    candidates.append(dict(candidates[0]))
                if change == "orphan": doc.targets[0].detail_row = "OLD-REMOVED"
                before = copy.deepcopy(doc)
                with self.assertRaisesRegex(frappe.ValidationError, "fila vinculada"):
                    preserved_rows(doc, candidates)
                self.assertEqual(doc, before)

    def test_company_difference_and_unlinked_new_rows_remain_available(self):
        doc, candidates = self.case()
        difference = frappe._dict(name="DIFF", row_kind="Diferencia documentada", difference_amount_usd=-.01)
        doc.detail_rows.append(difference)
        doc.targets.append(frappe._dict(detail_row="DIFF", complementary_item="CORE", amount_usd=-.01))
        candidates.append(dict(client_number="NEW", deducted_usd=10, amount_usd=10))
        result = preserved_rows(doc, candidates)
        self.assertEqual(result[-1], difference)
        self.assertEqual(result[-2]["client_number"], "NEW")

    def test_application_instructions_require_existing_client_row_only_when_detail_exists(self):
        rows = [frappe._dict(name="ROW"), frappe._dict(name="DIFF", row_kind="Diferencia documentada")]
        for claim in ("H:APP", "C:COL"):
            for detail in (None, "MISSING", "DIFF"):
                instructions = [dict(deposit_id="DEP", claim_id=claim, detail_row=detail)]
                with self.assertRaisesRegex(frappe.ValidationError, "sin una fila"):
                    validate_application_rows("DEP", rows, instructions, "DEP")
                validate_application_rows("DEP", [], instructions, "DEP")
            validate_application_rows("DEP", rows, [dict(deposit_id="DEP", claim_id=claim, detail_row="ROW")], "DEP")
        validate_application_rows("DEP", rows, [dict(deposit_id="DEP", claim_id="X:COMP")], "DEP")
        validate_application_rows("DEP", rows, [dict(deposit_id="OTHER", claim_id="H:APP")], "DEP")

    def test_reimport_failure_does_not_change_document_or_call_save(self):
        doc, candidates = self.case()
        doc.update(name="DEP", detail_hash="old", employer="EMP", allocation_detail="[]", save=Mock(), set=Mock(), append=Mock())
        candidates[0]["deducted_usd"] = 147.16
        before = copy.deepcopy(doc.detail_rows)
        with patch("credinomina_reconciliation.client_credit.guard_detail_replacement"), \
             patch.object(controller, "allowed_employers", return_value=["EMP"]), \
             patch.object(controller, "load_client_index", return_value=[]), \
             patch.object(controller, "load_detail_loan_clients", return_value={}), \
             patch.object(controller, "choose_detail_client", return_value=(None, "")), \
             self.assertRaises(frappe.ValidationError):
            controller._apply_remittance_detail(doc, candidates, b"new", "new.xlsx")
        self.assertEqual(doc.detail_rows, before)
        doc.save.assert_not_called()
        doc.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
