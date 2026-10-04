"""Export-only access and date scope; no database writes."""
import unittest
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import patch

import frappe
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import control_credinomina as page
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as statement


class ExportScopeTests(unittest.TestCase):
    def test_all_years_export_accepts_dashboard_label(self):
        response = frappe._dict()
        with patch.object(page, '_build_control_data', return_value={"year": "Todos", "periods": []}), \
             patch.object(frappe, 'has_permission', return_value=True), \
             patch.object(frappe, 'get_list', return_value=[]) as read, \
             patch.object(frappe, 'local', SimpleNamespace(response=response, flags=frappe._dict(in_test=False))), \
             patch.object(page, 'now_datetime', return_value=datetime(2026, 10, 2)), \
             patch.object(frappe, 'db', SimpleNamespace(get_single_value=lambda *a: 'dd/mm/yyyy')), \
             patch.object(statement, 'execute', return_value=([], [], 'Nota')) as report, \
             patch('credinomina_reconciliation.control_export.build_control_workbook', return_value=b'workbook') as workbook:
            page.export_control_excel('Todos', 'A')
        report.assert_called_once_with({'view_mode': 'Resumen', 'employer': 'A'})
        self.assertEqual(workbook.call_args.args[0]['company_statement'], {'columns': [], 'rows': [], 'message': 'Nota'})
        self.assertEqual(read.call_args.kwargs['filters'], [['period', 'is', 'not set'], ['employer', '=', 'A']])
        self.assertIn('Todos', response.filename)

    def test_unlinked_exceptions_have_company_and_exclusive_year_end(self):
        response = frappe._dict()
        captured = []
        def get_list(doctype, **kwargs):
            captured.append((doctype, kwargs))
            return []
        with patch.object(page, '_build_control_data', return_value={"year": 2025, "periods": []}), \
             patch.object(frappe, 'has_permission', return_value=True), \
             patch.object(frappe, 'get_list', side_effect=get_list), \
             patch.object(frappe, 'local', SimpleNamespace(response=response, flags=frappe._dict(in_test=False))), \
             patch.object(page, 'now_datetime', return_value=datetime(2026, 10, 2)), \
             patch.object(frappe, 'db', SimpleNamespace(get_single_value=lambda *a: 'dd/mm/yyyy')), \
             patch.object(statement, 'execute', return_value=(statement.get_columns(), [{'employer': 'Empresa A', 'balance_usd': 1.5}], 'Nota')) as report, \
             patch('credinomina_reconciliation.control_export.build_control_workbook', return_value=b'workbook') as workbook:
            page.export_control_excel(2025, 'Empresa A')
        report.assert_called_once_with({'view_mode': 'Resumen', 'employer': 'Empresa A',
                                       'from_date': '2025-01-01', 'to_date': '2025-12-31'})
        self.assertEqual(workbook.call_args.args[0]['company_statement']['rows'], [{'employer': 'Empresa A', 'balance_usd': 1.5}])
        filters = next(kwargs['filters'] for doctype, kwargs in captured if doctype == 'CN Reconciliation Exception')
        self.assertIn(['creation', '>=', '2025-01-01'], filters)
        self.assertIn(['creation', '<', '2026-01-01'], filters)
        self.assertIn(['employer', '=', 'Empresa A'], filters)
        self.assertEqual(response.filecontent, b'workbook')
        core_filters = next(kwargs['filters'] for doctype, kwargs in captured if doctype == 'CN Complementary Item')
        self.assertEqual(core_filters['employer'], 'Empresa A')
        self.assertEqual(core_filters['source_date'], ['between', ['2025-01-01', '2025-12-31']])
        self.assertEqual(core_filters['accounting_source_key'], ['is', 'set'])

    def test_employer_export_keeps_both_unlinked_routes_and_excludes_other_company(self):
        def get_list(doctype, **kwargs):
            if doctype == 'CN Accounting Import':
                return [frappe._dict(name='I1', employer='A'), frappe._dict(name='I2', employer='B')]
            return []
        def get_all(doctype, **kwargs):
            if doctype == 'CN Source Row' and kwargs.get('filters', {}).get('historical_period') == ['is', 'not set']:
                return [frappe._dict(name='H', parent='I1', event_date='2025-04-01', amount=10),
                        frappe._dict(name='O', parent='I1', event_date='2026-09-01', amount=20, processing_route='Operativa'),
                        frappe._dict(name='B', parent='I2', event_date='2025-04-01', amount=30)]
            return []
        with patch.object(frappe, 'has_permission', return_value=True), \
             patch.object(frappe, 'get_list', side_effect=get_list), \
             patch.object(frappe, 'get_all', side_effect=get_all), \
             patch.object(page, 'get_cash_deposits', return_value=[]):
            data = page._build_control_data('Todos', 'A', full_export=True)
        self.assertEqual([r.name for r in data['unassigned_historical_applications']], ['H'])
        self.assertEqual([r.name for r in data['unassigned_operational_applications']], ['O'])
        self.assertEqual(data['unassigned_historical_applications'][0].employer, 'A')
