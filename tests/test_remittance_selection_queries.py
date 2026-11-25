import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation.remittance_selection import _historical_sources, get_pending_targets


class Record(frappe._dict):
    def as_dict(self):
        return dict(self)


class PendingTargetQueryTests(unittest.TestCase):
    def test_explicit_client_override_lists_same_company_complements_without_client_record(self):
        detail = Record(name="ROW", employer="EMP", client_name="JENIFER", client_number="",
                        amount_usd=25.94, pending_usd=25.94, manual_client_override=1,
                        matched_targets="[]")
        deposit = Record(name="DEP", employer="EMP", amount_usd=25.94, docstatus=1,
                         modified="2026-11-24", detail_rows=[detail], targets=[], allocation_detail="")
        deposit.check_permission = lambda *_: None
        deposit._assert_open_related_periods = lambda: None
        items = [Record(name=name, employer=employer, amount_usd=51.87, period="",
                        source_client_name="JENIFER", description="Ajuste", reference="2415616",
                        voucher="001016652", client_number="", loan_number="",
                        generic_distribution=int(name == "GENERIC"))
                 for name, employer in (("SAME", "EMP"), ("OTHER", "OTHER"), ("GENERIC", "OTHER"))]

        def get_all(doctype, **_kwargs):
            return items if doctype == "CN Complementary Item" else []

        with patch.object(frappe, "get_doc", return_value=deposit), \
             patch.object(frappe, "get_list", return_value=[]), \
             patch.object(frappe, "get_all", side_effect=get_all), \
             patch.object(frappe, "has_permission", return_value=True), \
             patch("credinomina_reconciliation.remittance_selection.allowed_employers", return_value={"EMP", "OTHER"}), \
             patch("credinomina_reconciliation.remittance_selection.reconciliation_companies", return_value=["EMP", "OTHER"]), \
             patch("credinomina_reconciliation.deposit_accounting_sources.assert_usable_cash"), \
             patch("credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import._allocate_complementary_items", return_value={}), \
             patch("credinomina_reconciliation.complementary_distribution.attach_company_scopes"), \
             patch("credinomina_reconciliation.complementary_distribution.company_scope", return_value={"EMP", "OTHER"}), \
             patch("credinomina_reconciliation.complementary_remainders.cash_capacity", return_value=51.87), \
             patch("credinomina_reconciliation.client_credit.load_credits", return_value=[]), \
             patch("credinomina_reconciliation.credit_application.funding_totals", return_value={}), \
             patch("credinomina_reconciliation.client_registry.load_client_index") as client_index:
            result = get_pending_targets("DEP", detail_row_name="ROW")
            client_index.assert_not_called()
            detail.manual_client_override = 0
            client_index.return_value = []
            with patch("credinomina_reconciliation.deposit_identity.load_detail_loan_clients", return_value={}), \
                 patch("credinomina_reconciliation.paying_employers.choose_detail_client", return_value=(None, "sin cliente")):
                with self.assertRaisesRegex(frappe.ValidationError, "No se pudo verificar"):
                    get_pending_targets("DEP", detail_row_name="ROW")

        self.assertTrue(result["manual_client_override"])
        self.assertEqual(result["detail_available_cents"], 2594)
        self.assertEqual({row["complementary_item"] for row in result["rows"]}, {"SAME", "GENERIC"})
        self.assertTrue(all(row["employer"] == "EMP" for row in result["rows"]))
        self.assertEqual(result["rows"][0]["pending_cents"], 5187)

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
