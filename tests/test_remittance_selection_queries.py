import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation.remittance_selection import _historical_sources


class PendingTargetQueryTests(unittest.TestCase):
    def test_empty_periods_or_unselected_applications_do_not_load_imports(self):
        for periods, selected in [
            ({}, None),
            ({"H": frappe._dict(reconciliation_mode="Historica")}, {("C", "P", "ROW")}),
        ]:
            with patch.object(frappe, "has_permission", return_value=True), \
                 patch.object(frappe, "get_all") as rows, patch.object(frappe, "get_doc") as docs:
                self.assertEqual(list(_historical_sources(periods, selected)), [])
            rows.assert_not_called()
            docs.assert_not_called()

    def test_only_relevant_permission_checked_parents_are_loaded(self):
        allowed = Mock()
        allowed.has_permission.return_value = True
        denied = Mock()
        denied.has_permission.return_value = False
        periods = {"H": frappe._dict(reconciliation_mode="Historica"),
                   "P": frappe._dict(reconciliation_mode="Operativa")}
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(frappe, "get_all", return_value=["VISIBLE", "DENIED", "HIDDEN"]) as rows, \
             patch.object(frappe, "get_list", return_value=["VISIBLE", "DENIED"]) as parents, \
             patch.object(frappe, "get_doc", side_effect=[allowed, denied]) as docs:
            self.assertEqual(list(_historical_sources(periods, {("H", "APP")})), [allowed])
        filters = rows.call_args.kwargs["filters"]
        self.assertEqual(filters["historical_period"], ["in", ["H", "P"]])
        self.assertEqual(filters["name"], ["in", ["APP"]])
        self.assertEqual(filters["effective"], 1)
        self.assertEqual(filters["match_status"], "Conciliado")
        self.assertEqual(parents.call_args.kwargs["filters"]["name"],
                         ["in", ["VISIBLE", "DENIED", "HIDDEN"]])
        self.assertEqual([call.args[1] for call in docs.call_args_list], ["VISIBLE", "DENIED"])
        allowed.has_permission.assert_called_once_with("read")
        denied.has_permission.assert_called_once_with("read")

    def test_no_read_permission_or_no_eligible_rows_skips_document_loading(self):
        periods = {"H": frappe._dict(reconciliation_mode="Historica")}
        for permitted in (True, False):
            with patch.object(frappe, "has_permission", return_value=permitted), \
                 patch.object(frappe, "get_all", return_value=[]), \
                 patch.object(frappe, "get_doc") as docs:
                self.assertEqual(list(_historical_sources(periods)), [])
            docs.assert_not_called()
