import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import accounting_period_scope as scope
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as api


class AccountingPeriodScopeTests(unittest.TestCase):
    def test_action_requires_default_period_and_preserves_cash(self):
        for period in (None, "P"):
            document = frappe._dict(employer="E", historical_period=period, check_permission=Mock())
            with patch.object(api.frappe, "get_doc", return_value=document), \
                 patch.object(api.frappe, "throw", side_effect=frappe.ValidationError), \
                 patch.object(api, "_reconcile_sources", return_value={}) as reconcile:
                if period:
                    api.reconcile_company_sources("I")
                    reconcile.assert_called_once_with("E", preserve_deposits=True, period_name="P")
                else:
                    with self.assertRaises(frappe.ValidationError):
                        api.reconcile_company_sources("I")
                    reconcile.assert_not_called()

    def test_default_does_not_override_rows_assigned_elsewhere(self):
        rows = [frappe._dict(name="A", event_type="Aplicacion"),
                frappe._dict(name="B", event_type="Aplicacion", historical_period="OTHER"),
                frappe._dict(name="C", event_type="Deposito")]
        document = frappe._dict(historical_period="P", rows=rows)
        with patch.object(api, "_source_linked_periods", side_effect=lambda row: [row.historical_period] if row.historical_period else []):
            self.assertEqual(scope.selected_rows(document, "P"), [rows[0]])

    def test_unassigned_import_row_linked_to_selected_period_is_included(self):
        row = frappe._dict(name="A", event_type="Aplicacion")
        with patch.object(api, "_source_linked_periods", return_value=["P"]):
            self.assertEqual(scope.selected_rows(frappe._dict(rows=[row]), "P"), [row])

    def test_split_application_cannot_silently_drop_other_period(self):
        row = frappe._dict(name="A", event_type="Aplicacion")
        with patch.object(api, "_source_linked_periods", return_value=["P", "OTHER"]), \
             patch.object(scope.frappe, "throw", side_effect=frappe.ValidationError):
            with self.assertRaises(frappe.ValidationError):
                scope.selected_rows(frappe._dict(rows=[row]), "P")

    def test_related_cash_keeps_recognition_evidence_even_without_allocations(self):
        period = frappe._dict(name="P", deduction_recognition_deposit="DEP")
        deposit = frappe._dict(name="DEP", docstatus=1)
        with patch("credinomina_reconciliation.complementary_cancellation._related_cash", return_value=[]), \
             patch.object(scope.frappe, "get_doc", return_value=deposit):
            self.assertEqual(scope.related_cash([period], [], []), [deposit])

    def test_cross_period_cash_evidence_is_read_only_context(self):
        period = frappe._dict(name="P")
        other = frappe._dict(name="OTHER")
        deposit = frappe._dict(allocation_detail='[{"tipo":"Cobranza","periodo":"P"},{"tipo":"Cobranza","periodo":"OTHER"}]')
        with patch.object(scope.frappe, "get_doc", return_value=other) as get_doc:
            self.assertEqual(scope.cash_evidence_periods([period], [deposit]), [period, other])
        get_doc.assert_called_once_with("CN Reconciliation Period", "OTHER")
