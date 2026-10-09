import json
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import period_pending as pending


class PeriodPendingTests(unittest.TestCase):
    def test_deposited_unapplied_payment_is_visible_without_mutating_balances(self):
        row = dict(name='COL4', parent='P', idx=4, expected_usd=28.43, applied_usd=0,
                   remitted_usd=0, loan_number='13997-1', client_number='7740', application_status='Pendiente')
        original = row.copy()
        payments = [dict(target_name='DEP', amount_usd=28.43, deposit_detail_row='DETAIL4')]
        result = pending.collection_issue(row, 'Cobranza', payments)
        self.assertEqual((result['applied'], result['paid'], result['pending']), (0, 28.43, 28.43))
        self.assertEqual(result['status'], 'Pago pendiente de aplicar')
        self.assertEqual(result['pending_label'], 'Por aplicar')
        self.assertEqual(result['pending_deposit'], 0)
        self.assertEqual(result['paid_identified'], 28.43)
        self.assertFalse(result['can_create_complementary'])
        self.assertEqual(result['deposit_evidence'][0]['name'], 'DEP')
        self.assertEqual(row, original)

    def test_pending_application_and_uncovered_application_are_separate(self):
        row = dict(name='COL', expected_usd=50, applied_usd=20, remitted_usd=10)
        payments = [dict(target_name='D1', amount_usd=12), dict(target_name='D2', amount_usd=18)]
        result = pending.collection_issue(row, 'Cobranza', payments)
        self.assertEqual((result['paid'], result['pending_application'], result['pending_deposit']), (40, 30, 10))
        self.assertEqual(result['pending'], 30)
        self.assertEqual(len(result['deposit_evidence']), 2)

    def test_shared_deposit_uses_all_visible_periods_and_hides_restricted_evidence(self):
        period = frappe._dict(name='P', employer='E', reconciliation_mode='Operativa', application_basis='Cobranza')
        deposit = frappe._dict(name='D', allocation_detail='[{"periodo":"P"},{"periodo":"Q"}]')
        other = frappe._dict(name='Q', employer='E', reconciliation_mode='Operativa', application_basis='Cobranza')
        with patch.object(frappe, 'get_list', return_value=[other]) as query, \
             patch('credinomina_reconciliation.pending_payments.load_pending_payments', return_value=[
                 {'period': 'P', 'collection_row': 'C'}, {'period': 'Q', 'collection_row': 'OTHER'},
             ]) as load:
            tasks, partial = pending.period_payment_evidence(period, [deposit])
            self.assertEqual(tasks, [{'period': 'P', 'collection_row': 'C'}])
            self.assertFalse(partial)
            self.assertEqual({p['name'] for p in load.call_args.args[0]}, {'P', 'Q'})
            self.assertEqual(query.call_args.kwargs['filters'], {'name': ['in', ['Q']]})
        with patch.object(frappe, 'get_list', return_value=[]), \
             patch('credinomina_reconciliation.pending_payments.load_pending_payments', return_value=[]) as load:
            tasks, partial = pending.period_payment_evidence(period, [deposit])
            self.assertTrue(partial)
            self.assertEqual(tasks, [])
            self.assertEqual(load.call_args.args[1], [])

    def test_partial_payment_replaces_stale_missing_collection_message(self):
        row = dict(name="APP-80", parent="CONTA-ACEITERA-9-2025-002", idx=80, installment_number="4",
                   net_applied_usd=25.96, historical_remitted_usd=25.95,
                   historical_balance_usd=0.01, deposit_match_status="Sin deposito",
                   deposit_match_reason="La aplicacion aun no se enlaza de forma unica con una cobranza.")
        original = row.copy()
        result = pending.application_issue(row)
        self.assertEqual((result["source_row"], result["installment_number"]), ("APP-80", "4"))
        self.assertEqual(result["status"], "Depósito parcial")
        self.assertEqual(result["reason"],
                         "La aplicación tiene US$ 25.95 vinculados a depósitos de un aplicado neto de US$ 25.96. "
                         "Quedan US$ 0.01 pendientes de cubrir.")
        self.assertEqual(row, original)
        operative = pending.application_issue({**row, "processing_route": "Operativa",
                                               "quality_status": "Pendiente de detalle"})
        self.assertEqual(operative["status"], "Conciliación 1 pendiente")
        self.assertEqual(operative["reason"], "Pendiente de detalle")
        unpaid = pending.application_issue({**row, "historical_remitted_usd": 0,
                                            "historical_balance_usd": 25.96})
        self.assertEqual(unpaid["status"], "Sin deposito")
        self.assertEqual(unpaid["reason"], row["deposit_match_reason"])

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
        self.assertFalse(result["can_create_complementary"])
        query.assert_not_called()

    def test_create_action_requires_permission_and_open_period(self):
        period = Mock(reconciliation_mode="Historica", status="Parcial", docstatus=0)
        period.name = "P"
        with patch.object(pending.frappe, "get_doc", return_value=period), \
             patch.object(pending.frappe, "has_permission", side_effect=lambda dt, action: action == "create") as permission:
            self.assertTrue(pending.get_period_pending("P")["can_create_complementary"])
            period.status = "Cerrado"
            self.assertFalse(pending.get_period_pending("P")["can_create_complementary"])
            period.status = "Parcial"
            period.docstatus = 2
            self.assertFalse(pending.get_period_pending("P")["can_create_complementary"])
            period.docstatus = 0
            permission.side_effect = lambda *_: False
            self.assertFalse(pending.get_period_pending("P")["can_create_complementary"])


if __name__ == "__main__":
    unittest.main()
