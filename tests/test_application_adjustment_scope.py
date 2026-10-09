import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import application_adjustments as adjustments
from credinomina_reconciliation import complementary_cancellation as scoped
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item.cn_complementary_item import CNComplementaryItem


class ApplicationAdjustmentScopeTests(unittest.TestCase):
    def test_confirmation_does_not_run_company_reconciliation(self):
        doc = frappe._dict(employer="EMP", related_application="APP")
        with patch.object(adjustments, "reconcile_adjustment") as reconcile, \
             patch.object(engine, "_reconcile_sources") as company:
            CNComplementaryItem._reconcile_application(doc)
        reconcile.assert_called_once_with(doc)
        company.assert_not_called()

    def test_scope_uses_linked_and_saved_periods_and_keeps_deposits_immutable(self):
        doc = frappe._dict(employer="EMP", related_application="APP", period="SEPTEMBER",
            adjustment_periods='["FIRST-HALF"]', adjustment_collection_rows='["COL"]')
        row = frappe._dict(name="APP")
        with patch.object(frappe, "get_doc", return_value=row), \
             patch.object(engine, "_source_linked_periods", return_value=["SEPTEMBER"]), \
             patch.object(frappe, "db", Mock(get_value=Mock(return_value="SECOND-HALF"))), \
             patch.object(scoped, "reconcile_scoped", return_value={"saved_imports": 1}) as reconcile:
            result = adjustments.reconcile_adjustment(doc)
        reconcile.assert_called_once_with(doc, dict(companies=["EMP"],
            periods=["FIRST-HALF", "SECOND-HALF", "SEPTEMBER"], applications=["APP"], deposits=[]),
            action="confirmar el ajuste")
        self.assertEqual(result, {"saved_imports": 1})

    def test_unassigned_application_does_not_expand_to_company_periods(self):
        doc = frappe._dict(employer="EMP", related_application="APP")
        with patch.object(frappe, "get_doc", return_value=frappe._dict(name="APP")), \
             patch.object(engine, "_source_linked_periods", return_value=[]), \
             patch.object(scoped, "reconcile_scoped") as reconcile:
            adjustments.reconcile_adjustment(doc)
        self.assertEqual(reconcile.call_args.args[1], dict(companies=["EMP"], periods=[], applications=["APP"], deposits=[]))

    def test_closed_affected_period_is_still_rejected(self):
        scope = dict(companies=["EMP"], periods=["SEPTEMBER"], applications=["APP"], deposits=[])
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(scoped, "lock_cash_pool"), \
             patch.object(frappe, "get_doc", return_value=frappe._dict(status="Cerrado")), \
             patch.object(frappe, "throw", side_effect=frappe.ValidationError) as reject:
            with self.assertRaises(frappe.ValidationError):
                scoped.reconcile_scoped(frappe._dict(category="Ajuste de aplicación"), scope, action="confirmar el ajuste")
        self.assertIn("confirmar el ajuste", reject.call_args.args[0])
