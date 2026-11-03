import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import document_reopening as reopen
from credinomina_reconciliation import reconciliation_trace as trace
from credinomina_reconciliation.rounding import money


def reject(message, *args):
    raise ValueError(message)


class DocumentReopeningTests(unittest.TestCase):
    def document(self, doctype=reopen.ITEM, status=1):
        return frappe._dict(doctype=doctype, name="DOC", docstatus=status, modified="STAMP",
            category="Ajuste de aplicación", flags=frappe._dict(cancellation_scope={"applications": ["A"]}),
            meta=frappe._dict(is_submittable=doctype != reopen.IMPORT),
            check_permission=Mock(), cancel=Mock(), _prepare_deactivation=Mock(), on_trash=Mock())

    def test_unconfirm_goes_to_draft_without_cancellation_or_imported_removal_bypass(self):
        item = self.document()
        def draft(doc, values):
            self.assertEqual(values, dict(review_status="Ajuste pendiente de confirmar"))
            doc.docstatus = 0
            return "BEFORE"
        with patch.object(frappe, "db", Mock(exists=Mock(return_value=False))) as db, \
             patch.object(frappe, "get_doc", return_value=item), patch.object(reopen, "_draft", side_effect=draft), \
             patch.object(reopen, "_audit") as audit, patch("credinomina_reconciliation.manual_origin_link.guard_removal"), \
             patch("credinomina_reconciliation.complementary_cancellation.reconcile_scoped", return_value={"periods": ["P"]}) as recalc, \
             patch.object(trace, "capture_item_confirmation") as capture:
            result = reopen.unconfirm_complement("DOC", "STAMP", "Corrección")
        self.assertEqual(result, dict(name="DOC", docstatus=0, deposits=[], periods=["P"]))
        self.assertEqual([c.args[0] for c in item.check_permission.call_args_list], ["write", "cancel"])
        item._prepare_deactivation.assert_called_once_with()
        item.cancel.assert_not_called()
        self.assertFalse(item.flags.get("cn_unconfirming"))
        self.assertEqual(recalc.call_args.kwargs, dict(action="desconfirmar la partida"))
        self.assertEqual(recalc.call_args.args[0].docstatus, 0)
        capture.assert_called_once_with(item, "on_unconfirm")
        audit.assert_called_once_with(item, "BEFORE", "Desconfirmación de partida", "Corrección")
        db.commit.assert_not_called()
        db.rollback.assert_not_called()

    def test_failure_reverses_every_write_and_never_marks_item_cancelled(self):
        item = self.document()
        with patch.object(reopen, "_load", return_value=item), \
             patch.object(frappe, "db", Mock()) as db, patch.object(reopen, "_draft"), patch.object(reopen, "_audit") as audit, \
             patch("credinomina_reconciliation.manual_origin_link.guard_removal"), \
             patch("credinomina_reconciliation.complementary_cancellation.reconcile_scoped", side_effect=ValueError("Cash changed")), \
             self.assertRaisesRegex(ValueError, "Cash changed"):
            reopen.unconfirm_complement("DOC", "STAMP", "Corrección")
        db.rollback.assert_called_once_with(save_point="cn_reopen_document")
        audit.assert_not_called()
        item.cancel.assert_not_called()

    def test_stale_wrong_status_workflow_and_unauthorized_requests_write_nothing(self):
        for status, stamp, workflow in ((0, "STAMP", False), (2, "STAMP", False), (1, "OLD", False), (1, "STAMP", True)):
            item = self.document(status=status); item.modified = stamp
            with self.subTest(status=status, workflow=workflow), \
                 patch.object(frappe, "db", Mock(exists=Mock(return_value=workflow))) as db, \
                 patch.object(frappe, "get_doc", return_value=item), patch.object(frappe, "throw", side_effect=reject), \
                 self.assertRaises(ValueError):
                reopen.unconfirm_complement("DOC", "STAMP", "Motivo")
            db.set_value.assert_not_called()
            item._prepare_deactivation.assert_not_called()
        item = self.document(); item.check_permission.side_effect = frappe.PermissionError
        with patch.object(frappe, "db", Mock()) as db, patch.object(frappe, "get_doc", return_value=item), \
             self.assertRaises(frappe.PermissionError):
            reopen.undo_cancellation(reopen.ITEM, "DOC", "STAMP", "Motivo")
        db.set_value.assert_not_called()

    def test_restore_requires_delete_cancel_and_core_role_and_never_reconciles(self):
        item = self.document(status=2)
        with patch.object(reopen, "_load", return_value=item), patch.object(frappe, "db", Mock()), \
             patch("credinomina_reconciliation.accounting_evidence.guard_imported_removal") as role, \
             patch.object(reopen, "_draft", return_value="OLD") as draft, patch.object(reopen, "_audit") as audit, \
             patch("credinomina_reconciliation.complementary_cancellation.reconcile_scoped") as recalc:
            result = reopen.undo_cancellation(reopen.ITEM, "DOC", "STAMP", "Cancelación accidental")
        self.assertEqual(result, dict(name="DOC", docstatus=0))
        self.assertEqual([c.args[0] for c in item.check_permission.call_args_list], ["delete", "cancel"])
        role.assert_called_once_with(item, "restaurar")
        draft.assert_called_once_with(item, dict(review_status="Ajuste pendiente de confirmar"), {})
        audit.assert_called_once_with(item, "OLD", "Cancelación deshecha", "Cancelación accidental")
        recalc.assert_not_called()
        item.cancel.assert_not_called()

    def test_core_role_failure_prevents_restoration(self):
        with patch.object(reopen, "_load", return_value=self.document(status=2)), patch.object(frappe, "db", Mock()) as db, \
             patch("credinomina_reconciliation.accounting_evidence.guard_imported_removal", side_effect=frappe.PermissionError), \
             patch.object(reopen, "_draft") as draft, self.assertRaises(frappe.PermissionError):
            reopen.undo_cancellation(reopen.ITEM, "DOC", "STAMP", "Motivo")
        draft.assert_not_called()
        db.rollback.assert_called_once()

    def test_legacy_import_reopens_inactive_and_checks_links(self):
        doc = self.document(reopen.IMPORT, 2)
        with patch.object(reopen, "_load", return_value=doc), patch.object(frappe, "db", Mock()), \
             patch("credinomina_reconciliation.accounting_evidence.guard_imported_removal"), \
             patch.object(reopen, "_draft") as draft, patch.object(reopen, "_audit"):
            reopen.undo_cancellation(reopen.IMPORT, "DOC", "STAMP", "Motivo")
        doc.on_trash.assert_called_once_with()
        draft.assert_called_once_with(doc, dict(status="Borrador"), {"CN Source Row": dict(effective=0, match_status="Pendiente")})

    def test_unconfirmation_trace_reverses_each_adjustment_without_cancellation_event(self):
        item = self.document()
        item.related_application = "A"; item.application_adjustment_usd = 129.03
        item.application_adjustment_history = '[{"related_application":"B","application_adjustment_usd":129.03}]'
        with patch.object(trace, "append") as append:
            trace.capture_item_confirmation(item, "on_unconfirm")
        self.assertEqual([c.args[3] for c in append.call_args_list], ["A", "B"])
        self.assertTrue(all(c.kwargs["after"] == money(-129.03) for c in append.call_args_list))
        self.assertTrue(all(c.kwargs["description"] == "Desconfirmación de ajuste" for c in append.call_args_list))
        self.assertTrue(all(c.kwargs["key"][0] == "on_unconfirm" for c in append.call_args_list))
