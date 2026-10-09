import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import control_deposits, control_summary
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import control_credinomina as control


class ControlLazyTests(unittest.TestCase):
    def test_month_cards_and_detail_use_financial_period_status(self):
        period = frappe._dict(name='P', employer='E', payroll_month='2026-09-01',
            reconciliation_mode='Operativa', status='Conciliado', expected_usd=715.66,
            deducted_usd=715.66, applied_usd=715.65, remitted_usd=715.65, exception_count=0)
        row = frappe._dict(name='R', parent='P', expected_usd=38.90, deducted_usd=38.90,
            applied_usd=38.89, remitted_usd=38.89, deduction_status='Deduccion total',
            application_status='Aplicado y remitido')
        def get_list(doctype, **kwargs):
            if doctype == 'CN Reconciliation Period':
                self.assertIn('status_before_close', kwargs['fields'])
                return [period]
            return []
        def get_all(doctype, **kwargs):
            return [row] if doctype == 'CN Collection Row' else []
        for summary_only in (True, False):
            for status, before, exceptions, expected in (
                ('Conciliado', '', 0, 'conciliado'),
                ('Cerrado', 'Conciliado', 0, 'conciliado'),
                ('Parcial', '', 0, 'parcial'),
                ('Cerrado', 'Parcial', 0, 'parcial'),
                ('Conciliado', '', 1, 'diferencia'),
            ):
                period.update(status=status, status_before_close=before, exception_count=exceptions)
                with self.subTest(summary=summary_only, status=status, before=before, exceptions=exceptions), \
                     patch.object(control.frappe, 'has_permission', return_value=True), \
                     patch.object(control.frappe, 'get_list', side_effect=get_list), \
                     patch.object(control.frappe, 'get_all', side_effect=get_all), \
                     patch.object(control, 'collection_summaries', return_value={'P': {'employer_gap_usd': 0.01}}), \
                     patch.object(control, 'historical_difference_counts', return_value={'P': 0}):
                    result = control._build_control_data('Todos', summary_only=summary_only, detail_period='P')['periods'][0]
                self.assertEqual(result['control_state'], expected)
                self.assertEqual(result['employer_gap_usd'], 0.01)
                self.assertEqual((result['expected_usd'], result['applied_usd'], result['remitted_usd']),
                                 (715.66, 715.65, 715.65))

    def test_historical_counts_do_not_include_unreadable_imports(self):
        rows = [frappe._dict(historical_period="P", parent="READABLE", total=3),
                frappe._dict(historical_period="P", parent="PRIVATE", total=7)]
        with patch.object(control_summary.frappe, "has_permission", return_value=True), \
             patch.object(control_summary.frappe, "db", Mock(sql=Mock(return_value=rows))), \
             patch.object(control_summary.frappe, "get_list", return_value=["READABLE"]):
            self.assertEqual(control_summary.historical_difference_counts(["P"])["P"], 3)

    def test_dashboard_requests_summary_but_excel_requests_full_data(self):
        with patch.object(control, "_build_control_data", return_value={"year": "Todos"}) as build:
            self.assertEqual(control.get_control_data("Todos", "A")["work_scope"], "Todos")
        build.assert_called_once_with("Todos", "A", summary_only=True)

    def test_work_overview_ignores_calendar_year_but_keeps_employer(self):
        with patch.object(control, "_build_control_data", return_value={"work_scope": "Todos"}) as build:
            self.assertEqual(control.get_work_overview("A")["work_scope"], "Todos")
        build.assert_called_once_with("Todos", "A", summary_only=True, detail_section="work_overview")
        with patch.object(control, "_build_control_data", return_value={"year": 2026}):
            self.assertEqual(control.get_control_data(2026)["work_scope"], "calendar")

    def test_period_detail_checks_permission_before_loading(self):
        doc = Mock()
        doc.check_permission.side_effect = PermissionError
        with patch.object(control.frappe, "get_doc", return_value=doc), \
             patch.object(control, "_build_control_data") as build:
            with self.assertRaises(PermissionError):
                control.get_period_detail("PRIVATE")
        build.assert_not_called()

    def test_period_detail_is_scoped_and_returns_only_modal_data(self):
        values = {field: [field] for field in ("rows", "historical_rows", "exceptions", "surpluses", "rounding_movements")}
        doc = Mock()
        with patch.object(control.frappe, "get_doc", return_value=doc), \
             patch.object(control, "_build_control_data", return_value={"periods": [{**values, "employer": "A"}]}) as build:
            result = control.get_period_detail("PA")
        doc.check_permission.assert_called_once_with("read")
        build.assert_called_once_with("Todos", detail_period="PA")
        self.assertEqual(result, {"name": "PA", "detail_loaded": True, **values})

    def test_deposit_detail_checks_permission_and_filters_one_deposit(self):
        doc = Mock()
        with patch.object(control.frappe, "get_doc", return_value=doc), \
             patch.object(control, "get_cash_deposits", return_value=[{"name": "DEP", "destinations": []}]) as query:
            result = control.get_deposit_detail("DEP")
        doc.check_permission.assert_called_once_with("read")
        query.assert_called_once_with(None, deposit_name="DEP")
        self.assertTrue(result["detail_loaded"])
        doc.check_permission.side_effect = PermissionError
        with patch.object(control.frappe, "get_doc", return_value=doc), patch.object(control, "get_cash_deposits") as query:
            with self.assertRaises(PermissionError):
                control.get_deposit_detail("DEP")
        query.assert_not_called()

    def test_operational_modal_includes_payment_evidence_without_changing_allocated_cash(self):
        row = frappe._dict(name='C', remitted_usd=0, applied_usd=0)
        period = dict(reconciliation_mode='Operativa', rows=[row], historical_rows=[], exceptions=[], surpluses=[], rounding_movements=[])
        tasks = [dict(collection_row='C', target_name='DEP', amount_usd=28.43)]
        with patch.object(control.frappe, 'get_doc', return_value=Mock()), \
             patch.object(control, '_build_control_data', return_value={'periods': [period]}), \
             patch.object(control.frappe, 'has_permission', return_value=True), \
             patch('credinomina_reconciliation.period_pending.load_period_deposits', return_value=[]) as deposits, \
             patch('credinomina_reconciliation.period_pending.period_payment_evidence', return_value=(tasks, False)):
            result = control.get_period_detail('P')
        deposits.assert_called_once_with('P')
        self.assertEqual(result['pending_application_usd'], 28.43)
        self.assertEqual(result['rows'][0]['pending_payment_usd'], 28.43)
        self.assertEqual(result['rows'][0]['pending_payment_deposits'], [{'name': 'DEP', 'amount_usd': 28.43}])
        self.assertEqual((row.remitted_usd, row.applied_usd), (0, 0))
        with patch.object(control.frappe, 'get_doc', return_value=Mock()), \
             patch.object(control, '_build_control_data', return_value={'periods': [period]}), \
             patch.object(control.frappe, 'has_permission', return_value=False), \
             patch('credinomina_reconciliation.period_pending.load_period_deposits') as deposits:
            result = control.get_period_detail('P')
        deposits.assert_not_called()
        self.assertTrue(result['payment_evidence_restricted'])
        self.assertIsNone(result['pending_application_usd'])
        self.assertIsNone(result['rows'][0]['pending_payment_usd'])

    def test_cash_summary_does_not_resolve_people_and_keeps_all_amounts(self):
        deposit = frappe._dict(name="D", employer="A", deposit_date="2025-05-01", amount_usd=120,
            allocated_usd=110, justified_surplus_usd=10, result="Parcial con saldo a favor", allocation_detail='''[
            {"tipo":"Aplicacion historica","periodo":"P","importe_usd":100},
            {"tipo":"Partida complementaria","partida":"X","importe_usd":10}]''')
        # Parent metadata remains permission-scoped; client identities are deferred.
        def get_list(doctype, **kwargs):
            if doctype == "CN Complementary Item":
                return [frappe._dict(name="X", period="Q", category="Cobranza administrativa")]
            return [frappe._dict(name=n, payroll_month="2025-04-01") for n in ("P", "Q")]
        with patch.object(control_deposits.frappe, "has_permission", return_value=True), \
             patch.object(control_deposits.frappe, "get_list", side_effect=get_list), \
             patch.object(control_deposits, "_load_credit_people", side_effect=AssertionError("Loaded people")):
            row, = control_deposits.get_cash_deposits(2025, include_details=False, deposits=[deposit])
        self.assertEqual((row["total_usd"], row["credits_usd"], row["other_usd"], row["credit_balance_usd"]), (120, 100, 10, 10))
        self.assertTrue(row["shared"])
        self.assertNotIn("destinations", row)

    def test_pagination_is_bounded_and_rejects_unknown_sections(self):
        rows = [{"name": str(i), "priority": i % 3} for i in range(250)]
        with patch.object(control, "_build_control_data", return_value={"work_items": rows}) as build:
            page = control.get_control_rows("work_items", "Todos", "A", 100)
        self.assertEqual(page, {"rows": rows[100:200], "count": 250, "action_count": 250, "overdue_count": 84})
        build.assert_called_once_with("Todos", "A", summary_only=True, detail_section="work_items")
        with patch.object(control.frappe, "throw", side_effect=ValueError), patch.object(control, "_build_control_data") as build:
            with self.assertRaises(ValueError):
                control.get_control_rows("anything")
        build.assert_not_called()
