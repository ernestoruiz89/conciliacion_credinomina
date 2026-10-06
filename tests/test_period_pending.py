import json
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import period_pending as pending


class PeriodPendingTests(unittest.TestCase):
    def test_historical_partial_and_mixed_settlement(self):
        row = dict(parent="I", idx=9, net_applied_usd=137.33, historical_remitted_usd=111.32,
                   historical_balance_usd=26.01, deposit_match_status="Depósito parcial")
        result = pending.application_issue(row)
        self.assertEqual((result["row"], result["applied"], result["paid"], result["pending"]), (9, 137.33, 111.32, 26.01))
        for status in pending.SETTLED:
            self.assertIsNone(pending.application_issue({**row, "deposit_match_status": status}))

    def test_only_selected_quality_issue_survives_cash_settlement(self):
        row = dict(parent="P", idx=1, expected_usd=100, deducted_usd=90,
                   applied_usd=90, remitted_usd=90, deduction_status="Deduccion parcial",
                   application_status="Aplicado y remitido")
        result = pending.collection_issue(row, "Cobranza")
        self.assertEqual(result["pending"], 0)
        self.assertIn("Aplicación insuficiente", result["reason"])
        self.assertIsNone(pending.collection_issue(row, "Detalle de empresa"))
        self.assertEqual(pending.collection_issue({**row, "remitted_usd": 80}, "Detalle de empresa")["pending"], 10)

    def test_signed_rounding_and_complementary_do_not_invent_a_shortfall(self):
        row = dict(expected_usd=100, applied_usd=90, complementary_usd=10, remitted_usd=99.99,
                   rounding_adjustment_usd=-0.01, deduction_status="Deduccion total", application_status="Aplicado y remitido")
        self.assertIsNone(pending.collection_issue(row, "Cobranza"))

    def test_deposit_exact_link_and_separate_global_balance(self):
        deposit = dict(name="D", amount_usd=1601.63, unclassified_usd=118.99, result="Revisar detalle",
                       allocation_detail=json.dumps([{"periodo": "P", "tipo": "Cobranza"}, {"periodo": "P"}]))
        result = pending.deposit_issue(deposit, "P")
        self.assertEqual((result["paid"], result["pending"]), (1601.63, 118.99))
        self.assertIn("otros períodos", result["reason"])
        self.assertIsNone(pending.deposit_issue(deposit, "P2"))
        self.assertIsNone(pending.deposit_issue({**deposit, "unclassified_usd": 0, "result": "Conciliado"}, "P"))
        for invalid in ("bad json", "null", "{}", "[1]"):
            self.assertIsNone(pending.deposit_issue({**deposit, "allocation_detail": invalid}, "P"))

    def test_permission_is_checked_before_loading(self):
        doc = Mock()
        doc.check_permission.side_effect = PermissionError
        with patch.object(pending.frappe, "get_doc", return_value=doc), patch.object(pending.frappe, "get_all") as query:
            with self.assertRaises(PermissionError):
                pending.get_period_pending("PRIVATE")
            query.assert_not_called()

    def test_deposit_applied_includes_only_executed_credit_allocations_all_periods(self):
        deposit = dict(name="D", detail_periods=[{"period": "P"}], amount_usd=200, unclassified_usd=19.99,
                       result="Parcial", allocation_detail=json.dumps([
                           {"tipo": "Cobranza", "periodo": "P", "importe_usd": 100.10},
                           {"tipo": "Aplicacion historica", "periodo": "Q", "importe_usd": 49.90},
                           {"tipo": "Partida complementaria", "importe_usd": 30},
                           {"tipo": "Movimiento de conciliación", "importe_usd": 0.01},
                       ]), targets=[{"amount_usd": 999}])
        result = pending.deposit_issue(deposit, "P")
        self.assertEqual(result["applied"], 150)
        self.assertEqual((result["paid"], result["pending"]), (200, 19.99))
        self.assertIn("todos sus períodos", result["reason"])
        for detail in ("[]", "null", "{}", "invalid", "[1]"):
            self.assertEqual(pending.deposit_issue({**deposit, "allocation_detail": detail}, "P")["applied"], 0)

    def test_permission_scoped_rows_filter_and_paginate_without_writes(self):
        period = Mock(name="unused", reconciliation_mode="Historica")
        period.name = "P"
        rows = [frappe._dict(parent="VISIBLE", idx=i, client_name="Marión", deposit_match_status="Sin deposito",
                            historical_balance_usd=1) for i in range(1, 62)]
        rows.append(frappe._dict(parent="PRIVATE", idx=62, client_name="Private"))
        with patch.object(pending.frappe, "get_doc", return_value=period), \
             patch.object(pending.frappe, "has_permission", return_value=True), \
             patch.object(pending.frappe, "get_all", side_effect=lambda doctype, **kwargs: rows if doctype == "CN Source Row" else []) as query, \
             patch.object(pending, "readable_imports", return_value={"VISIBLE"}), \
             patch.object(pending.frappe, "get_list", return_value=[]):
            result = pending.get_period_pending("P", search="marion", kind="Aplicación", start=50)
            self.assertEqual((result["total"], result["count"], len(result["rows"])), (61, 61, 11))
            self.assertEqual(result["rows"][0]["row"], 51)
            self.assertEqual(result["restricted"], ["CN Accounting Import"])
            self.assertEqual(query.call_args_list[0].kwargs["filters"]["historical_period"], "P")
            self.assertEqual(pending.get_period_pending("P", kind="Depósito")["count"], 0)
        period.save.assert_not_called()

    def test_no_permissions_explicitly_marks_partial_view(self):
        period = Mock(reconciliation_mode="Historica")
        period.name = "P"
        with patch.object(pending.frappe, "get_doc", return_value=period), \
             patch.object(pending.frappe, "has_permission", return_value=False), \
             patch.object(pending.frappe, "get_all") as query:
            result = pending.get_period_pending("P")
        self.assertEqual(len(result["restricted"]), 2)
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
