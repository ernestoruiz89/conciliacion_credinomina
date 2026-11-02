import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation.patches.v1_0 import backfill_period_cash_breakdown as migration
from credinomina_reconciliation.patches.v1_0 import backfill_period_financial_totals, backfill_period_coverage_components


class PeriodCashBreakdownPatchTests(unittest.TestCase):
    def test_patch_changes_only_breakdown_even_for_closed_periods(self):
        period = frappe._dict(name="P", status="Cerrado", save=Mock(),
            applied_total_usd=100, remitted_total_usd=100, pending_usd=0)
        values = dict(deposit_coverage_usd=90, complementary_coverage_usd=10, negative_complementary_usd=-10,
            cash_breakdown_known=1, cash_breakdown_note="", pending_usd=0, applied_total_usd=100,
            remitted_total_usd=100, status="Conciliado")
        with patch.object(frappe, "reload_doc"), patch.object(frappe, "get_all", side_effect=[["P"], []]), \
             patch.object(frappe, "get_doc", return_value=period), \
             patch.object(migration, "PeriodTotalsContext") as context, patch.object(frappe.db, "set_value") as save:
            context.return_value.values.return_value = values
            migration.execute()
        self.assertEqual(save.call_args.args[:2], ("CN Reconciliation Period", "P"))
        self.assertEqual(save.call_args.args[2], {field: values[field] for field in migration.FIELDS})
        self.assertFalse(save.call_args.kwargs["update_modified"])
        self.assertEqual(period.status, "Cerrado")
        period.save.assert_not_called()

    def test_unknown_components_use_storage_zero_with_explicit_unknown_flag(self):
        period = frappe._dict(name="P")
        values = dict(deposit_coverage_usd=None, complementary_coverage_usd=None,
            negative_complementary_usd=None, cash_breakdown_known=0, cash_breakdown_note="Sin atribución",
            applied_total_usd=0, remitted_total_usd=0, pending_usd=0)
        with patch.object(frappe, "reload_doc"), patch.object(frappe, "get_all", side_effect=[["P"], []]), \
             patch.object(frappe, "get_doc", return_value=period), \
             patch.object(migration, "PeriodTotalsContext") as context, patch.object(frappe.db, "set_value") as save:
            context.return_value.values.return_value = values
            migration.execute()
        self.assertEqual(save.call_args.args[2], dict(deposit_coverage_usd=0,
            complementary_coverage_usd=0, negative_complementary_usd=0,
            cash_breakdown_known=0, cash_breakdown_note="Sin atribución"))

    def test_stale_saved_balances_are_preserved_without_certifying_a_cash_split(self):
        period = frappe._dict(name="P", status="Cerrado", applied_total_usd=100,
            remitted_total_usd=90, pending_usd=10, save=Mock())
        values = dict(deposit_coverage_usd=90, complementary_coverage_usd=10,
            negative_complementary_usd=-10, cash_breakdown_known=1, cash_breakdown_note="",
            applied_total_usd=100, remitted_total_usd=100, pending_usd=0)
        with patch.object(frappe, "reload_doc"), patch.object(frappe, "get_all", side_effect=[["P"], []]), \
             patch.object(frappe, "get_doc", return_value=period), \
             patch.object(migration, "PeriodTotalsContext") as context, patch.object(frappe.db, "set_value") as save:
            context.return_value.values.return_value = values
            migration.execute()
        stored = save.call_args.args[2]
        self.assertEqual(set(stored), set(migration.FIELDS))
        self.assertEqual((stored["cash_breakdown_known"], stored["deposit_coverage_usd"],
            stored["complementary_coverage_usd"], stored["negative_complementary_usd"]), (0, 0, 0, 0))
        self.assertIn("totales guardados", stored["cash_breakdown_note"])
        self.assertEqual((period.status, period.remitted_total_usd, period.pending_usd), ("Cerrado", 90, 10))
        period.save.assert_not_called()

    def test_older_pending_migrations_accept_indeterminate_components(self):
        values = dict(deposit_coverage_usd=None, complementary_coverage_usd=None,
            negative_complementary_usd=None, cash_breakdown_known=0, cash_breakdown_note="Sin atribución",
            applied_total_usd=100, remitted_total_usd=100, pending_usd=0)
        for module in (backfill_period_financial_totals, backfill_period_coverage_components):
            with self.subTest(module=module.__name__), patch.object(frappe, "reload_doc"), \
                 patch.object(frappe, "get_all", side_effect=[["P"], []]), \
                 patch.object(frappe, "get_doc", return_value=frappe._dict(name="P")), \
                 patch.object(module, "PeriodTotalsContext") as context, patch.object(frappe.db, "set_value") as save:
                context.return_value.values.return_value = values
                module.execute()
            stored = save.call_args.args[2]
            self.assertEqual((stored["deposit_coverage_usd"], stored["complementary_coverage_usd"]), (0, 0))
            self.assertFalse(any(value is None for field, value in stored.items() if field.endswith('_usd')))
            self.assertFalse(save.call_args.kwargs["update_modified"])
