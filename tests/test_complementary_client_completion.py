import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import complementary_related_client as api


def source(**values):
    return frappe._dict(dict(doctype="CN Complementary Item", name="ITEM", modified="V1", docstatus=0,
        category="Por clasificar", employer="IMMSA", client_name="", client_number="",
        source_client_name="RONAL FLORE PERE", loan_number="", check_permission=Mock(), add_comment=Mock()) | values)


class ComplementaryClientCompletionTests(unittest.TestCase):
    def complete(self, item, modified="V1", client=None, guard_error=None):
        client = client or frappe._dict(name="3253", client_name="RONALD FLORES PEREZ", client_number="3253", check_permission=Mock())
        identity = dict(client_name="RONALD FLORES PEREZ", numbers={"3253"})
        with patch.object(frappe, "get_doc", side_effect=[item, client]) as get, \
             patch.object(frappe, "has_permission", return_value=True), \
             patch.object(frappe, "throw", side_effect=frappe.ValidationError), \
             patch.object(api, "resolve", return_value=("IMMSA", identity)) as resolve, \
             patch.object(api, "_guard_completion", side_effect=guard_error) as guard, \
             patch.object(frappe.db, "set_value") as write, \
             patch.object(frappe, "clear_document_cache"):
            try:
                result = api.complete_client_fields("ITEM", "3253", modified)
                self.assertEqual(get.call_args_list[0].kwargs, dict(for_update=True))
                resolve.assert_called_once_with(item, "3253")
                item.check_permission.assert_any_call("write")
                client.check_permission.assert_called_once_with("read")
                return result, write, guard
            except frappe.ValidationError:
                write.assert_not_called()
                item.add_comment.assert_not_called()
                raise

    def test_only_blank_fields_are_written_with_audit_without_touching_financial_data(self):
        item = source(amount_usd=19.25, pending_usd=19.25, reclassification_allocations="[]")
        result, write, guard = self.complete(item)
        self.assertEqual(result["updated_fields"], dict(client_name="RONALD FLORES PEREZ", client_number="3253"))
        write.assert_called_once_with("CN Complementary Item", "ITEM", result["updated_fields"])
        self.assertEqual(item.source_client_name, "RONAL FLORE PERE")
        self.assertEqual(item.amount_usd, 19.25)
        self.assertEqual(item.pending_usd, 19.25)
        self.assertEqual(item.reclassification_allocations, "[]")
        item.add_comment.assert_called_once()
        self.assertEqual(guard.call_args.args[2], "3253")

    def test_existing_fields_are_never_overwritten(self):
        for values, changes in [
            (dict(client_name="Nombre original distinto"), dict(client_number="3253")),
            (dict(client_number="003253"), dict(client_name="RONALD FLORES PEREZ")),
        ]:
            with self.subTest(values=values):
                item = source(**values)
                result, write, _ = self.complete(item)
                self.assertEqual(result["updated_fields"], changes)
                self.assertEqual(dict(item) | values, dict(item))
                self.assertEqual(set(write.call_args.args[2]), set(changes))

    def test_stale_cancelled_company_generic_and_full_items_do_not_write(self):
        for values, modified in [({}, "OLD"), (dict(docstatus=2), "V1"),
            (dict(category="Saldo a favor de la empresa"), "V1"), (dict(generic_distribution=1), "V1"),
            (dict(origin_deposit="DEP"), "V1"), (dict(category="Diferencia por tolerancia"), "V1"),
            (dict(client_name="EXISTS", client_number="3253"), "V1")]:
            with self.subTest(values=values, modified=modified), self.assertRaises(frappe.ValidationError):
                self.complete(source(**values), modified)

    def test_current_client_identity_and_immutable_guards_are_rechecked(self):
        changed = frappe._dict(name="3253", client_name="NEW NAME", client_number="3253", check_permission=Mock())
        with self.assertRaises(frappe.ValidationError):
            self.complete(source(), client=changed)
        with self.assertRaises(frappe.ValidationError):
            self.complete(source(), guard_error=frappe.ValidationError("Identidad confirmada protegida"))

    def test_submitted_items_need_submit_permission_as_well_as_write(self):
        item = source(docstatus=1)
        self.complete(item)
        item.check_permission.assert_any_call("submit")
        item = source(check_permission=Mock(side_effect=frappe.PermissionError))
        with patch.object(frappe, "get_doc", return_value=item), patch.object(api, "resolve") as resolve, \
             patch.object(frappe.db, "set_value") as write:
            with self.assertRaises(frappe.PermissionError):
                api.complete_client_fields("ITEM", "3253", "V1")
            resolve.assert_not_called()
            write.assert_not_called()

    def test_credit_beneficiary_cannot_be_replaced_even_when_name_is_empty(self):
        with patch.object(frappe, "throw", side_effect=frappe.ValidationError):
            with self.assertRaises(frappe.ValidationError):
                api._guard_completion(source(credit_client="OTHER"), dict(client_name="RONALD"), "3253")

    def test_actual_financial_identity_guards_reject_changes_to_frozen_records(self):
        entry = dict(operation_id="OP", kind="Pago pendiente contra aplicación", target="APP", parent_document="IMPORT",
                     employer="IMMSA", effective_date="2026-01-01", amount_usd=1)
        for values in [dict(reclassification_allocations=[entry]),
                       dict(compensations=[frappe._dict(operation_id="OP")]),
                       dict(docstatus=1, category="Saldo a favor del cliente")]:
            with self.subTest(values=values):
                item = source(flags=frappe._dict(), **values)
                candidate = frappe._dict(dict(item) | dict(client_number="3253"))
                item.as_dict = lambda: dict(item)
                with patch.object(frappe, "get_doc", return_value=candidate), \
                     patch.object(frappe, "throw", side_effect=frappe.ValidationError):
                    with self.assertRaises(frappe.ValidationError):
                        api._guard_completion(item, dict(client_number="3253"), "3253")

    def test_actual_guards_allow_plain_imported_unclassified_item_enrichment(self):
        item = source(flags=frappe._dict())
        item.as_dict = lambda: dict(item)
        candidate = frappe._dict(dict(item) | dict(client_name="RONALD FLORES PEREZ", client_number="3253"))
        with patch.object(frappe, "get_doc", return_value=candidate):
            api._guard_completion(item, dict(client_name="RONALD FLORES PEREZ", client_number="3253"), "3253")


if __name__ == "__main__":
    unittest.main()
