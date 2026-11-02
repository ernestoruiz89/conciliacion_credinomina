"""The compact header endpoint validates attachment access and keeps row data out."""
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot import cn_credit_portfolio_snapshot as portfolio


class PortfolioHeaderSaveTests(unittest.TestCase):
    def test_only_attached_readable_file_and_editable_header_are_forwarded(self):
        attachment = Mock(is_remote_file=False)
        saved = frappe._dict(source_file="/private/files/new.xlsx", notes="Nota", disabled=0,
            modified="2026-11-02 10:00:00", modified_by="USER", file_hash="UNCHANGED",
            row_count=10000, rows=[{"credit_number": "1-1"}])
        with patch.object(frappe, "get_doc", return_value=attachment) as read, \
             patch.object(portfolio, "_update_portfolio_header", return_value=saved) as update, \
             patch.object(portfolio, "prune_previous_files", return_value=["OLD-FILE"]) as prune:
            result = portfolio.save_portfolio_header("CUT", saved.source_file, "Nota", 0, "OLD")
        read.assert_called_once_with("File", {"file_url": saved.source_file,
            "attached_to_doctype": "CN Credit Portfolio Snapshot", "attached_to_name": "CUT"})
        attachment.check_permission.assert_called_once_with("read")
        update.assert_called_once_with("CUT", dict(source_file=saved.source_file, notes="Nota", disabled=0), "OLD")
        prune.assert_called_once_with("CUT", saved.source_file)
        self.assertEqual(result["removed_attachments"], ["OLD-FILE"])
        self.assertEqual(set(result), {"source_file", "notes", "disabled", "modified", "modified_by", "removed_attachments"})
        self.assertNotIn("rows", result)

    def test_unreadable_or_unattached_file_cannot_change_the_header(self):
        for error in (frappe.PermissionError, frappe.DoesNotExistError):
            with self.subTest(error=error), patch.object(frappe, "get_doc", side_effect=error), \
                 patch.object(portfolio, "_update_portfolio_header") as update:
                with self.assertRaises(error):
                    portfolio.save_portfolio_header("CUT", "/private/files/other.xlsx", "", 0, "OLD")
                update.assert_not_called()

    def test_parent_permissions_and_stale_document_failures_are_not_suppressed(self):
        for error in (frappe.PermissionError, frappe.TimestampMismatchError, frappe.UpdateAfterSubmitError):
            with self.subTest(error=error), patch.object(frappe, "get_doc", return_value=Mock(is_remote_file=False)), \
                 patch.object(portfolio, "_update_portfolio_header", side_effect=error), \
                 patch.object(portfolio, "prune_previous_files") as prune:
                with self.assertRaises(error):
                    portfolio.save_portfolio_header("CUT", "/private/files/new.xlsx", "", 0, "OLD")
                prune.assert_not_called()

    def test_an_unavailable_upload_cannot_replace_or_remove_the_previous_file(self):
        attachment = Mock(is_remote_file=False, exists_on_disk=Mock(return_value=False))
        with patch.object(frappe, "get_doc", return_value=attachment), \
             patch.object(frappe, "throw", side_effect=frappe.ValidationError), \
             patch.object(portfolio, "_update_portfolio_header") as update, \
             patch.object(portfolio, "prune_previous_files") as prune:
            with self.assertRaises(frappe.ValidationError):
                portfolio.save_portfolio_header("CUT", "/private/files/pending.xlsx", "", 0, "OLD")
        update.assert_not_called()
        prune.assert_not_called()
