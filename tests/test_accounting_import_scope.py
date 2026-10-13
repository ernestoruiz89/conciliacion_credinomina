import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import accounting_import_scope as scope
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


class Row(frappe._dict):
    def as_dict(self):
        return dict(self)


class AccountingImportScopeTests(unittest.TestCase):
    def test_related_applications_are_copied_without_changing_paid_claim_identity(self):
        document = frappe._dict(name="CURRENT", employer="E", historical_period="P", rows=[], check_permission=Mock())
        period = frappe._dict(name="P", employer="E", status="Pendiente", check_permission=Mock())
        paid = Row(name="PAID", event_type="Aplicacion", historical_period="", processing_route="Operativa",
                   application_allocation_detail='[{"collection_row_id":"C","period":"P","amount_usd":50}]')
        unrelated = Row(name="OTHER", event_type="Aplicacion", historical_period="OTHER")
        other = frappe._dict(name="FIXED", employer="E", historical_period="P", rows=[paid, unrelated])
        with patch.object(scope.frappe, "get_doc", side_effect=[document, period]), \
             patch.object(scope.frappe, "get_all", return_value=["P"]), \
             patch.object(engine, "_source_linked_periods", side_effect=lambda row: ["P"] if row.name == "PAID" else []), \
             patch("credinomina_reconciliation.complementary_cancellation._load_imports", return_value=[document, other]):
            imported, periods, fixed = scope.load_context("CURRENT", "E", ["E"], ["RENAMED", "P"])
        self.assertIs(imported, document)
        self.assertEqual(periods, [period])
        self.assertEqual([row.name for row in fixed], ["PAID"])
        self.assertEqual(fixed[0].historical_period, "")
        self.assertEqual(fixed[0]._source_import, "FIXED")
        fixed[0].historical_period = "CHANGED"
        self.assertEqual(paid.historical_period, "")
        document.check_permission.assert_called_once_with("write")
        period.check_permission.assert_called_once_with("write")

    def test_unassigned_reload_with_no_old_links_has_no_company_wide_fallback(self):
        document = frappe._dict(name="I", employer="E", rows=[], check_permission=Mock())
        with patch.object(scope.frappe, "get_doc", return_value=document), \
             patch.object(scope.frappe, "get_all") as query, \
             patch("credinomina_reconciliation.complementary_cancellation._load_imports") as load:
            self.assertEqual(scope.load_context("I", "E", ["E"], []), (document, [], []))
        query.assert_not_called()
        load.assert_not_called()

    def test_foreign_and_closed_periods_are_rejected_before_loading_sources(self):
        for employer, status in [("OTHER", "Pendiente"), ("E", "Cerrado")]:
            document = frappe._dict(name="I", employer="E", rows=[], check_permission=Mock())
            period = frappe._dict(name="P", employer=employer, status=status, check_permission=Mock())
            with self.subTest(employer=employer, status=status), \
                 patch.object(scope.frappe, "get_doc", side_effect=[document, period]), \
                 patch.object(scope.frappe, "get_all", return_value=["P"]), \
                 patch.object(scope.frappe, "throw", side_effect=frappe.ValidationError), \
                 patch("credinomina_reconciliation.complementary_cancellation._load_imports") as load:
                with self.assertRaises(frappe.ValidationError):
                    scope.load_context("I", "E", ["E"], ["P"])
                load.assert_not_called()

    def test_previous_scope_includes_split_links_and_header(self):
        document = frappe._dict(historical_period="NEW", rows=[Row(name="A"), Row(name="B")])
        with patch.object(engine, "_source_linked_periods", side_effect=[["OLD", "SECOND"], ["SECOND"]]):
            self.assertEqual(scope.linked_periods(document), ["NEW", "OLD", "SECOND"])
