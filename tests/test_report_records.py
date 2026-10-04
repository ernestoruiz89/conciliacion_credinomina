import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import report_records as paging


class ReportRecordsTests(unittest.TestCase):
    def test_parent_reads_all_pages_and_uses_permission_aware_query(self):
        population = [frappe._dict(name=str(i)) for i in range(1101)]
        def query(doctype, **kwargs):
            start = kwargs['limit_start']
            self.assertEqual(kwargs['limit_page_length'], 500)
            self.assertEqual(kwargs['filters'], {'employer': 'E'})
            self.assertEqual(kwargs['order_by'], 'payroll_month desc, name asc')
            return population[start:start + 500]
        with patch.object(frappe, 'get_list', side_effect=query) as parents, patch.object(frappe, 'get_all') as unrestricted:
            result = list(paging.records('CN Reconciliation Period', fields=['name'],
                filters={'employer': 'E'}, order_by='payroll_month desc'))
        self.assertEqual(result, population)
        self.assertEqual(parents.call_count, 3)
        unrestricted.assert_not_called()

    def test_child_batches_never_read_outside_readable_parent_scope(self):
        calls = []
        names = ['P' + str(i) for i in range(1001)]
        def query(doctype, **kwargs):
            calls.append(kwargs)
            return []
        with patch.object(frappe, 'get_all', side_effect=query):
            result = list(paging.child_records('CN Collection Row', names, 'CN Reconciliation Period',
                'collection_rows', fields=['name'], filters={'parent': 'forged', 'client_number': '42'}))
        self.assertEqual(result, [])
        self.assertEqual(len(calls), 3)
        queried = []
        for call in calls:
            scope = call['filters']
            self.assertEqual(scope['parenttype'], 'CN Reconciliation Period')
            self.assertEqual(scope['parentfield'], 'collection_rows')
            self.assertEqual(scope['client_number'], '42')
            queried.extend(scope['parent'][1])
        self.assertEqual(set(queried), set(names))
        self.assertEqual(len(queried), len(names))

    def test_empty_parents_no_query_and_exact_page_boundary_not_truncated(self):
        with patch.object(frappe, 'get_all') as query:
            self.assertEqual(list(paging.child_records('Child', [], 'Parent', 'rows', fields=['name'])), [])
            query.assert_not_called()
        with patch.object(frappe, 'get_list', side_effect=[[{'name': str(i)} for i in range(500)], []]) as query:
            self.assertEqual(len(list(paging.records('Parent', fields=['name']))), 500)
            self.assertEqual(query.call_count, 2)

    def test_summary_includes_period_after_old_ten_thousand_limit(self):
        from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion import resumen_de_conciliacion as report
        def query(doctype, **kwargs):
            if doctype != 'CN Reconciliation Period':
                return []
            start = kwargs['limit_start']
            return [frappe._dict(name='P' + str(i), employer='E', reconciliation_mode='Historica',
                    applied_usd=1, payroll_month='2025-01-01')
                    for i in range(start, min(start + kwargs['limit_page_length'], 10001))]
        with patch.object(frappe, 'get_list', side_effect=query):
            columns, rows = report.execute()
        self.assertEqual(len(rows), 10001)
        self.assertEqual(sum(row['historical_pending_usd'] for row in rows), 10001)
        self.assertEqual(rows[-1]['name'], 'P10000')

    def test_operational_aging_includes_row_after_old_hundred_thousand_limit(self):
        from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos import antiguedad_de_saldos as report
        period = frappe._dict(name='P', employer='E', payroll_month='2025-01-01', cutoff_date='2025-01-31')
        def query(doctype, **kwargs):
            start = kwargs['limit_start']
            self.assertEqual(kwargs['filters']['parent'], ['in', ['P']])
            return [frappe._dict(name='R' + str(i), parent='P', client_name='C', loan_number='L',
                        expected_usd=1, deducted_usd=0, deduction_status='No deducido')
                    for i in range(start, min(start + kwargs['limit_page_length'], 100001))]
        with patch.object(frappe, 'get_list', return_value=[period]), patch.object(frappe, 'get_all', side_effect=query):
            columns, rows, message, chart, totals = report.execute({
                'balance_type': 'Cobranza no deducida (informativo)', 'as_of_date': '2026-10-03'})
        self.assertEqual(len(rows), 100001)
        self.assertEqual(totals[0]['value'], 100001)


if __name__ == '__main__':
    unittest.main()
