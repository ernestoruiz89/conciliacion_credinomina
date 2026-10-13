"""Regressions from the cross-screen audit: financial state is not workflow state."""
import unittest
from unittest.mock import patch

import frappe
from credinomina_reconciliation import control_kpis, follow_up_queue, complementary_balances
from credinomina_reconciliation.company_transactions import deposit_state, deposit_month_amounts, monthly_counts, application_month_amounts
from credinomina_reconciliation.period_presentation import financial_position
from credinomina_reconciliation.conciliacion_credinomina.report.transacciones_por_empresa import transacciones_por_empresa as transactions


class CrossViewConsistencyTests(unittest.TestCase):
    def test_compensated_period_shows_original_and_covered_in_both_modes(self):
        for mode in ('Historica', 'Operativa'):
            for status in ('Conciliado', 'Cerrado'):
                period = dict(reconciliation_mode=mode, status=status, status_before_close='Conciliado',
                    applied_usd=0, remitted_usd=0, applied_total_usd=100, remitted_total_usd=100,
                    pending_usd=0, exception_count=1)
                result = financial_position(period)
                self.assertEqual(result, dict(financial_applied_usd=100, financial_covered_usd=100,
                    financial_pending_usd=0, control_state='conciliado'))
                self.assertEqual(period['exception_count'], 1)

    def test_financial_totals_not_cash_and_no_netting_other_clients(self):
        self.assertEqual(financial_position(dict(status='Parcial', applied_total_usd=100,
            remitted_total_usd=100, pending_usd=20))['financial_pending_usd'], 20)
        self.assertEqual(financial_position(dict(applied_total_usd=100, remitted_total_usd=80,
            pending_usd=20, expected_usd=900))['control_state'], 'parcial')
        self.assertEqual(financial_position(dict(applied_usd=500, applied_total_usd=0,
            remitted_total_usd=0, pending_usd=0))['financial_applied_usd'], 0)

    def test_overallocated_deposit_has_no_misleading_percentage(self):
        row = dict(name='D', docstatus=1, employer='E', deposit_date='2025-04-01',
            amount_usd=100, allocated_usd=110, result='Revisar detalle')
        state = deposit_state(row)
        result = monthly_counts([dict(employer='E', date='2025-04-01', state=state)],
            2025, deposit_month_amounts([row]))[0]
        self.assertEqual(result['m04_state'], 'Revisar distribución')
        self.assertEqual(result['m04_review'], 1)
        self.assertIsNone(result['m04_percentage'])
        self.assertEqual(transactions.transaction_summary([dict(state=state)])[-1]['value'], 1)

    def test_legacy_import_company_resolved_per_row_and_same_in_drilldown(self):
        parent = frappe._dict(name='I', employer='', status='Importado')
        periods = [frappe._dict(name='P1', employer='E1'), frappe._dict(name='P2', employer='E2')]
        rows = [frappe._dict(name='R1', parent='I', historical_period='P1', event_date='2025-04-01',
            event_type='Aplicacion', currency='USD', amount=100, effective=1, match_status='Conciliado'),
            frappe._dict(name='R2', parent='I', historical_period='P2', event_date='2025-04-01',
            event_type='Aplicacion', currency='USD', amount=200, effective=1, match_status='Conciliado')]
        def listed(dt, **kw):
            if dt == 'CN Accounting Import':
                self.assertNotIn('employer', kw['filters'])
                return [parent]
            return periods if dt == 'CN Reconciliation Period' else []
        def children(dt, **kw):
            return [frappe._dict(row) for row in rows] if dt == 'CN Source Row' else []
        with patch.object(frappe, 'has_permission', return_value=True), patch.object(frappe, 'get_list', side_effect=listed), \
             patch.object(frappe, 'get_all', side_effect=children):
            for company, amount in [('E1', 100), ('E2', 200)]:
                records, sources, imports = transactions.load_transactions('Aplicaciones', False,
                    {'employer': company}, ['2025-01-01', '2025-12-31'])
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['employer'], company)
                values = application_month_amounts(sources, imports, {})
                self.assertEqual(values[(company, '2025-04')]['total_usd'], amount)
                detail = transactions.get_month_detail(2025, 4, company)
                self.assertEqual(detail['filtered_count'], 1)
                self.assertEqual(detail['totals']['original_usd'], amount)
        self.assertEqual(parent.employer, '')

    def test_kpi_reuses_statement_context_and_preserves_company_scope(self):
        source = dict(name='R', parent='I', event_type='Aplicacion', event_date='2025-04-01',
            historical_period='P', effective=1, match_status='Conciliado', currency='USD', amount=920.5)
        context = ([source], {'I': dict(employer='')}, {'P': dict(name='P', employer='E', payroll_month='2025-04-01')},
                   {}, {'E': dict(grace_days=10)})
        with patch.object(frappe, 'has_permission', return_value=True), \
             patch.object(control_kpis, 'load_application_context', return_value=context) as load:
            value = control_kpis.load_application_figures(None, 'E', '2026-12-31')
        load.assert_called_once_with('E')
        self.assertEqual(value['pending_usd'], 920.5)

    def test_exception_client_and_credit_search_uses_loaded_fields(self):
        item = dict(name='EX', employer='E', status='Abierta', exception_type='Diferencia',
            client_name='Ana Perez', client_number='123', loan_number='109277-1', description='Pago adicional')
        tasks = follow_up_queue.build_follow_up([], {}, [item])
        for search in ('Ana Perez', '123', '109277-1', 'Pago adicional'):
            self.assertEqual(len(follow_up_queue.filter_work(tasks, search=search)), 1)

    def test_list_balance_endpoint_scopes_permissions_and_separates_management(self):
        item = frappe._dict(name='C', category='Saldo a favor del cliente', docstatus=1, amount_usd=20,
            result='Saldo a favor documentado', credit_pending_usd=20, credit_management_status='Pendiente')
        with patch.object(frappe, 'has_permission', return_value=True), \
             patch.object(frappe, 'get_list', return_value=[item]) as query:
            result = complementary_balances.get_list_balances(['C'])
        self.assertEqual(query.call_args.kwargs['filters']['name'], ['in', ['C']])
        self.assertEqual(result['C']['state'], 'Conciliado')
        self.assertEqual(result['C']['pending_usd'], 0)
        self.assertEqual(result['C']['management_pending_usd'], 20)
        self.assertEqual(result['C']['management_status'], 'Pendiente')
        with patch.object(frappe, 'has_permission', return_value=False), patch.object(frappe, 'get_list') as query:
            self.assertEqual(complementary_balances.get_list_balances(['PRIVATE']), {})
            query.assert_not_called()


if __name__ == '__main__':
    unittest.main()
