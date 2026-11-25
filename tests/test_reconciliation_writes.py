import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.reconciliation_writes import update_child_results


class ReconciliationWriteTests(unittest.TestCase):
    def test_bulk_changes_only_requested_fields_and_invalidates_named_children(self):
        doctype = "CN Remittance Detail"
        changes = {
            "first": {"linked_usd": 1.02, "pending_usd": 0},
            "second": {"loan_number": "002-1"},
            "third": {"match_status": "Revisar"},
            "fourth": {"matched_targets": "[]", "company_credit_usd": 2},
        }
        large = {"matched_targets_summary": "A" * 20000}
        with patch.object(frappe, "db", Mock()) as db, patch.object(frappe, "cache", Mock()) as cache:
            db.value_cache = {doctype: {"old": 1}, "other": {"keep": 1}}
            update_child_results(doctype, {**changes, "long": large})
            db.set_value.assert_called_once_with(doctype, "long", large, update_modified=False)
            db.bulk_update.assert_called_once_with(doctype, changes, chunk_size=50, update_modified=False)
            keys = [frappe.get_document_cache_key(doctype, name) for name in changes]
            cache.delete_value.assert_called_once_with(keys)
            self.assertEqual(db.value_cache, {"other": {"keep": 1}})
            db.after_commit.add.call_args.args[0]()
            db.after_rollback.add.call_args.args[0]()
            self.assertEqual([call.args for call in cache.delete_value.call_args_list], [(keys,)] * 3)

    def test_older_frameworks_use_existing_single_row_writer(self):
        changes = {str(i): {"result": "Aplicada"} for i in range(6)}
        with patch.object(frappe, "db", Mock(spec=["set_value"])) as db:
            update_child_results("CN Remittance Target", changes)
            self.assertEqual([call.args for call in db.set_value.call_args_list],
                             [("CN Remittance Target", name, fields) for name, fields in changes.items()])
            self.assertTrue(all(call.kwargs == {"update_modified": False} for call in db.set_value.call_args_list))
