import copy
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import immaterial_differences as module
from credinomina_reconciliation.patches.v1_0 import classify_immaterial_differences as backfill


class ImmaterialDifferenceTests(unittest.TestCase):
    def test_signed_cent_only_and_manual_documentation_wins(self):
        expected = {"difference_reason": module.REASON, "difference_notes": module.NOTES}
        for amount in (0.01, -0.01, "0.0100000001", "-0.009999999"):
            self.assertEqual(module.automatic_documentation({}, (amount,)), expected)
        for amounts in ((0,), (.02,), (-.02,), (None,), (.01, 20), (), (.01, None)):
            self.assertEqual(module.automatic_documentation({}, amounts), {})
        for documentation in ({"difference_reason": "Manual"}, {"difference_notes": "Soporte manual"}, expected):
            self.assertEqual(module.automatic_documentation(documentation, (.01,)), {})

    def test_collection_first_stage_is_independent_and_unknown_fx_is_not_zero(self):
        rows = [frappe._dict(name="QUALITY", expected_usd=10, applied_usd=9.99),
                frappe._dict(name="CASH", expected_usd=10, applied_usd=10, remitted_usd=9.99),
                frappe._dict(name="UNKNOWN", expected_nio=366, applied_usd=.01),
                frappe._dict(name="LARGER", expected_usd=10, applied_usd=10.02, remitted_usd=10.01)]
        document = frappe._dict(doctype="CN Reconciliation Period", collection_rows=rows)
        eligible = [row.name for row, differences in module.candidates(document)
                    if module.automatic_documentation(row, differences)]
        self.assertEqual(eligible, ["QUALITY", "CASH"])

    def test_documentation_writes_only_labels_and_is_idempotent(self):
        row = frappe._dict(doctype="CN Remittance Detail", name="ROW", pending_usd=-.01,
                          amount_usd=10, linked_usd=10.01, matched_targets='[{"claim_id":"H:A"}]', match_status="Revisar")
        document = frappe._dict(doctype="CN Remittance Allocation", name="DEP", docstatus=1,
            amount_usd=10, unclassified_usd=.01, allocated_usd=9.99, detail_rows=[row], modified="v1",
            add_comment=Mock(), save=Mock())
        before = copy.deepcopy(dict(document))
        with patch("credinomina_reconciliation.difference_reasons.seed_reasons"), \
             patch.object(frappe.db, "get_value", return_value=frappe._dict(difference_reason="", difference_notes="")), \
             patch.object(frappe.db, "set_value") as write, \
             patch.object(frappe, "clear_document_cache"):
            self.assertEqual(module.classify_document(document), 2)
            self.assertEqual(module.classify_document(document), 0)
        self.assertEqual(write.call_count, 3)
        for call in write.call_args_list[:2]:
            self.assertEqual(set(call.args[2]), {"difference_reason", "difference_notes"})
            self.assertFalse(call.kwargs["update_modified"])
        self.assertEqual(set(write.call_args.args[2]), {"modified", "modified_by"})
        document.save.assert_not_called()
        for field in ("amount_usd", "unclassified_usd", "allocated_usd"):
            self.assertEqual(document[field], before[field])
        for field in ("pending_usd", "amount_usd", "linked_usd", "matched_targets", "match_status"):
            self.assertEqual(row[field], before["detail_rows"][0][field])

    def test_manual_documentation_saved_since_loading_is_never_overwritten(self):
        document = frappe._dict(doctype="CN Remittance Allocation", name="DEP", docstatus=1,
                                unclassified_usd=.01)
        with patch.object(frappe.db, "get_value", return_value=frappe._dict(
                difference_reason="Razón manual", difference_notes="Soporte concurrente")), \
             patch.object(frappe.db, "set_value") as write:
            self.assertEqual(module.classify_document(document), 0)
        write.assert_not_called()
        self.assertEqual(document.difference_notes, "Soporte concurrente")

    def test_cancelled_draft_and_out_of_scope_rows_are_untouched(self):
        for status in (0, 2):
            document = frappe._dict(doctype="CN Remittance Allocation", docstatus=status, unclassified_usd=.01)
            self.assertEqual(module.classify_document(document), 0)
        document = frappe._dict(doctype="CN Accounting Import", status="Importado", rows=[
            frappe._dict(name="A", event_type="Aplicacion", effective=1, historical_period="P", historical_balance_usd=.01)])
        self.assertEqual(module.classify_document(document, row_names={"OTHER"}), 0)

    def test_patch_visits_existing_documents_including_closed_periods_without_saving(self):
        closed = frappe._dict(doctype="CN Reconciliation Period", name="CLOSED", status="Cerrado")
        with patch.object(backfill, "seed_reasons"), \
             patch.object(backfill, "_pages", return_value=[frappe._dict(name="CLOSED")]) as pages, \
             patch.object(frappe, "get_doc", return_value=closed) as load, \
             patch.object(backfill, "classify_document") as classify:
            backfill.execute()
        self.assertEqual(classify.call_count, 3)
        self.assertTrue(all(call.kwargs["for_update"] for call in load.call_args_list))
        self.assertNotIn("status", pages.call_args_list[0].kwargs["filters"])
