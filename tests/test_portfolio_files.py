"""Prune only saved predecessors of this cut, with deferred shared-safe deletion."""
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import portfolio_files as files


class PortfolioFilesTests(unittest.TestCase):
    def test_history_selects_predecessors_but_not_current_or_unconfirmed_uploads(self):
        versions = [frappe._dict(data=frappe.as_json({"changed": [["source_file", previous, current]]}))
                    for previous, current in (("OLD", "OLDER"), ("OLDER", "CURRENT"), ("CURRENT", "OLD"))]
        old = frappe._dict(name="OLD-ID", file_url="OLD", attached_to_field="source_file")
        unrelated = frappe._dict(name="SUPPORT", file_url="OLDER", attached_to_field="other_support")
        def query(doctype, **kwargs):
            if doctype == "Version":
                return versions
            self.assertEqual(kwargs["filters"]["attached_to_name"], "CUT")
            self.assertEqual(kwargs["filters"]["file_url"], ["in", ["OLD", "OLDER"]])
            return [old, unrelated]
        db = Mock(exists=Mock(return_value=True), get_value=Mock(side_effect=[old, unrelated]))
        with patch.object(frappe, "db", db), patch.object(frappe, "get_all", side_effect=query), \
             patch.object(frappe, "get_doc", return_value=Mock(is_remote_file=False)), \
             patch.object(frappe, "clear_document_cache"), patch.object(frappe, "enqueue") as enqueue, \
             patch.object(frappe, "delete_doc") as delete:
            self.assertEqual(files.prune_previous_files("CUT", "CURRENT"), ["OLD-ID"])
        db.set_value.assert_called_once_with("File", "OLD-ID", dict(attached_to_doctype=None,
            attached_to_name=None, attached_to_field=None), update_modified=False)
        enqueue.assert_called_once_with("credinomina_reconciliation.portfolio_files._delete_detached_file",
            name="OLD-ID", url="OLD", queue="short", enqueue_after_commit=True)
        delete.assert_not_called()

    def test_no_cleanup_without_a_valid_current_attachment(self):
        with patch.object(frappe, "db", Mock(exists=Mock(return_value=False))), \
             patch.object(frappe, "get_all") as read, patch.object(frappe, "enqueue") as enqueue:
            self.assertEqual(files.prune_previous_files("CUT", "MISSING"), [])
        read.assert_not_called()
        enqueue.assert_not_called()

    def test_a_file_record_without_uploaded_contents_is_not_enough_for_cleanup(self):
        with patch.object(frappe, "db", Mock(exists=Mock(return_value="PENDING"))), \
             patch.object(frappe, "get_doc", return_value=Mock(is_remote_file=False,
                 exists_on_disk=Mock(return_value=False))), \
             patch.object(frappe, "get_all") as read, patch.object(frappe, "enqueue") as enqueue:
            self.assertEqual(files.prune_previous_files("CUT", "PENDING"), [])
        read.assert_not_called()
        enqueue.assert_not_called()

    def test_worker_preserves_restored_reassigned_or_shared_sources(self):
        for attachment, used in ((None, False),
                (frappe._dict(file_url="DIFFERENT"), False),
                (frappe._dict(file_url="OLD", attached_to_doctype=files.DOCTYPE, attached_to_name="CUT"), False),
                (frappe._dict(file_url="OLD"), True)):
            with self.subTest(attachment=attachment, used=used), \
                 patch.object(frappe, "db", Mock(get_value=Mock(return_value=attachment), exists=Mock(return_value=used))), \
                 patch.object(frappe, "delete_doc") as delete:
                files._delete_detached_file("OLD-ID", "OLD")
                delete.assert_not_called()

    def test_worker_uses_normal_deletion_hooks_only_for_an_unused_detached_source(self):
        with patch.object(frappe, "db", Mock(get_value=Mock(return_value=frappe._dict(file_url="OLD")),
                exists=Mock(return_value=False))), patch.object(frappe, "delete_doc") as delete:
            files._delete_detached_file("OLD-ID", "OLD")
        delete.assert_called_once_with("File", "OLD-ID", ignore_permissions=True)
