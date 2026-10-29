import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import bulk_period_closure as api
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as controller


class BulkPeriodClosureTests(unittest.TestCase):
    def test_candidates_respect_filters_permissions_and_include_all_pages(self):
        filters = [[api.PERIOD, "employer", "=", "EMPRESA"]]
        with patch.object(frappe, "has_permission") as permission, \
             patch.object(frappe, "get_list", return_value=[dict(name="P")]) as query:
            self.assertEqual(api.list_periods(filters=filters), [dict(name="P")])
        permission.assert_called_once_with(api.PERIOD, "write", throw=True)
        self.assertEqual(query.call_args.args, (api.PERIOD,))
        self.assertEqual(query.call_args.kwargs["filters"], filters + [["status", "=", "Conciliado"], ["docstatus", "<", 2]])
        self.assertEqual(query.call_args.kwargs["limit_page_length"], 0)
        self.assertEqual(len(filters), 1, "Do not mutate the caller's filters")

    def test_selection_takes_precedence_and_cannot_override_eligibility(self):
        with patch.object(frappe, "has_permission"), patch.object(frappe, "get_list") as query:
            api.list_periods(filters='[["status", "=", "Cerrado"]]', period_names='["P", "P", "Q"]')
        self.assertEqual(query.call_args.kwargs["filters"], [
            ["name", "in", ["P", "Q"]], ["status", "=", "Conciliado"], ["docstatus", "<", 2],
        ])

    def test_write_denial_stops_candidate_query(self):
        with patch.object(frappe, "has_permission", side_effect=frappe.PermissionError), \
             patch.object(frappe, "get_list") as query:
            with self.assertRaises(frappe.PermissionError):
                api.list_periods()
        query.assert_not_called()

    def test_changed_cancelled_and_read_only_periods_never_attempt_closure(self):
        for status, docstatus in [("Cerrado", 0), ("Parcial", 0), ("Conciliado", 2)]:
            with self.subTest(status=status, docstatus=docstatus), \
                 patch.object(frappe, "get_doc", return_value=frappe._dict(
                     name="P", status=status, docstatus=docstatus, check_permission=Mock())), \
                 patch.object(frappe, "throw", side_effect=frappe.ValidationError), \
                 patch.object(controller, "close_period") as close:
                with self.assertRaises(frappe.ValidationError):
                    api.close_reconciled_period("P")
                close.assert_not_called()
        doc = frappe._dict(name="P", status="Conciliado", docstatus=0,
                          check_permission=Mock(side_effect=frappe.PermissionError))
        with patch.object(frappe, "get_doc", return_value=doc), patch.object(controller, "close_period") as close:
            with self.assertRaises(frappe.PermissionError):
                api.close_reconciled_period("P")
            close.assert_not_called()

    def test_reuses_financial_checks_and_failure_propagates_for_request_rollback(self):
        doc = frappe._dict(name="P", status="Conciliado", docstatus=0, check_permission=Mock())
        with patch.object(frappe, "get_doc", return_value=doc), \
             patch.object(controller, "close_period", return_value=dict(period="P", status="Cerrado")) as close:
            self.assertEqual(api.close_reconciled_period("P")["status"], "Cerrado")
            close.assert_called_once_with("P")
            doc.check_permission.assert_called_once_with("write")
        with patch.object(frappe, "get_doc", return_value=doc), \
             patch.object(controller, "close_period", side_effect=frappe.ValidationError("Depósito pendiente")), \
             patch.object(frappe.db, "commit") as commit:
            with self.assertRaisesRegex(frappe.ValidationError, "Depósito pendiente"):
                api.close_reconciled_period("P")
            commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
