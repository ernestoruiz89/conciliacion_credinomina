from contextlib import ExitStack
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import bulk_accounting_import as bulk


def reject(message, *args, **kwargs):
    raise ValueError(message)


class RequiredBulkPortfolioTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(bulk, "_", side_effect=lambda text: text))
        self.stack.enter_context(patch.object(frappe, "throw", side_effect=reject))

    def test_blank_cut_rejected_before_file_read_or_enqueue(self):
        with patch.object(bulk, "_permissions"), patch.object(bulk, "_file") as file, \
             patch.object(bulk, "_enqueue") as enqueue:
            for value in (None, "", "   "):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Seleccione un Corte"):
                    bulk.preview_bulk_import("file.csv", "USD", portfolio_snapshot=value)
            file.assert_not_called()
            enqueue.assert_not_called()

    def test_planning_and_direct_block_creation_reject_legacy_missing_cut(self):
        with patch.object(bulk, "_permissions"), patch.object(bulk, "_file") as file, \
             patch.object(bulk, "readable_file") as readable:
            for action in (lambda: bulk._plan({}), lambda: bulk._create_imports({"groups": []}, {})):
                with self.assertRaisesRegex(ValueError, "Seleccione un Corte"):
                    action()
            file.assert_not_called()
            readable.assert_not_called()

    def test_confirm_and_resume_cannot_process_saved_batch_without_cut(self):
        for action in (bulk.confirm_bulk_import, bulk.resume_bulk_import):
            with patch.object(bulk, "_state", return_value={"options": {}}), \
                 patch("credinomina_reconciliation.accounting_batch_store.ensure_mutex"), \
                 patch.object(bulk, "_enqueue") as enqueue:
                with self.assertRaisesRegex(ValueError, "Seleccione un Corte"):
                    action("OLD-BATCH")
                enqueue.assert_not_called()

    def test_only_readable_imported_snapshots_are_accepted(self):
        snapshot = Mock()
        snapshot.get.return_value = 0
        with patch.object(frappe, "get_doc", return_value=snapshot):
            for status in ("Borrador", "Fallido"):
                snapshot.status = status
                with self.assertRaisesRegex(ValueError, "debe estar importado"):
                    bulk._required_portfolio_snapshot("CUT")
            for status in ("Importado", "Importado con alertas"):
                snapshot.status = status
                self.assertEqual(bulk._required_portfolio_snapshot(" CUT "), "CUT")
            snapshot.check_permission.assert_called_with("read")
            snapshot.check_permission.side_effect = frappe.PermissionError
            with self.assertRaises(frappe.PermissionError):
                bulk._required_portfolio_snapshot("CUT")

    def test_manual_company_cannot_silently_drop_selected_cut(self):
        with patch.object(frappe, "get_all", return_value=["A"]):
            bulk._validate_portfolio_companies("CUT", [{"employer": "A"}, {"employer": "NO IDENTIFICADA"}])
            with self.assertRaisesRegex(ValueError, "empresas: B"):
                bulk._validate_portfolio_companies("CUT", [{"employer": "B"}])
