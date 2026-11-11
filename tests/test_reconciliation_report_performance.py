"""Batched reads retain credit identity, permissions and complete populations."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation import reconciliation_report as core
from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion import resumen_de_conciliacion as report


class ReconciliationReportPerformanceTests(unittest.TestCase):
    def test_bulk_credit_lookup_keeps_legacy_numbers_cycles_and_duplicates(self):
        loans = [f'{number}-1' for number in range(1, 506)] + ['abc.7', 'a+b', '8-2']
        credits = [dict(credit_number=f'{number:07}-1', credit_status='Activo', employer='E')
                   for number in range(1, 506)]
        credits.extend([
            dict(credit_number='000001', credit_status='Cancelado', employer='OTHER'),
            dict(credit_number='ABC.7', credit_status='Activo', employer='E'),
            dict(credit_number='a+b', credit_status='Activo', employer='E'),
            dict(credit_number='0008-2', credit_status='Activo', employer='E'),
            # Canonicalization alone would incorrectly accept the first two.
            dict(credit_number=' 000001-1 ', employer='E'),
            dict(credit_number="'000001-1", employer='E'),
            dict(credit_number='1-2', employer='E'),
            dict(credit_number='abcX7', employer='E'),
            dict(credit_number='ab', employer='E'),
            dict(credit_number='999999-1', employer='E'),
        ])
        before = deepcopy(credits)
        with patch.object(core, 'child_records', return_value=credits) as query, \
                patch.object(core, 'get_credit_rows', return_value=credits[506:508]) as small_lookup:
            result = core._portfolio_rows('READABLE-CUT', loans)
        self.assertCountEqual(result, credits[:509])
        self.assertEqual(credits, before)
        self.assertEqual(query.call_args.args[1:4], (['READABLE-CUT'], 'CN Credit Portfolio Snapshot', 'rows'))
        self.assertEqual(small_lookup.call_args.kwargs['loans'], {'abc.7', 'a+b'})

    def test_small_lookup_deduplicates_padded_aliases_before_choosing_strategy(self):
        with patch.object(core, 'get_credit_rows', return_value=[]) as query, \
                patch.object(core, 'child_records') as bulk:
            core._portfolio_rows('CUT', ['000123-1', '123', '123-1'] * 501)
        self.assertEqual(query.call_args.kwargs['loans'], {'123-1'})
        self.assertEqual(query.call_args.kwargs['filters']['parent'], 'CUT')
        bulk.assert_not_called()

    def test_bulk_status_reads_all_pages_preserves_ambiguity_and_refreshes(self):
        period = dict(name='P', employer='E', payroll_month='2026-05-01', reconciliation_mode='Historica')
        details = [dict(name='P', employer='E', loan_number=f'{i}-1') for i in range(1, 506)]
        credits = [frappe._dict(credit_number=f'{i:06}-1', credit_status='Activo', employer='E')
                   for i in range(1, 505)]
        credits[2].employer = 'OTHER'
        credits[3].credit_status = ''
        credits.append(frappe._dict(credit_number='000002', credit_status='Cancelado', employer='OTHER'))

        def pages(doctype, **kwargs):
            self.assertEqual(doctype, 'CN Credit Portfolio Row')
            self.assertEqual(kwargs['filters'], {
                'parent': ['in', ['VISIBLE']], 'parenttype': 'CN Credit Portfolio Snapshot', 'parentfield': 'rows'})
            start = kwargs['limit_start']
            return credits[start:start + kwargs['limit_page_length']]

        with patch.object(frappe, 'has_permission', return_value=True), \
                patch.object(core, 'records', return_value=[dict(name='VISIBLE', report_date='2026-05-31')]), \
                patch.object(frappe, 'get_all', side_effect=pages) as query:
            core.add_portfolio_status([period], details)
            self.assertEqual(query.call_count, 2)
            self.assertEqual([row['portfolio_credit_status'] for row in details[:4]], [
                'Activo', 'Crédito duplicado en corte', 'Empresa distinta en corte', 'Sin estado en corte'])
            self.assertEqual(details[-1]['portfolio_credit_status'], 'Crédito no encontrado en corte')
            credits[0].credit_status = 'Cancelado'
            core.add_portfolio_status([period], details)
            self.assertEqual(details[0]['portfolio_credit_status'], 'Cancelado')
            self.assertEqual(query.call_count, 4)
        with patch.object(frappe, 'has_permission', return_value=False), \
                patch.object(core, 'records') as parents, patch.object(frappe, 'get_all') as children:
            core.add_portfolio_status([period], details)
            self.assertTrue(all(row['portfolio_credit_status'] == 'Sin corte de cartera aplicable' for row in details))
            parents.assert_not_called()
            children.assert_not_called()

    def test_detail_enriches_applications_and_pending_receipts_together(self):
        period = frappe._dict(name='P', employer='E', payroll_month='2026-05-01',
                              reconciliation_mode='Historica', applied_usd=20)
        application = dict(name='A', event_type='Aplicacion', historical_period='P', client_number='42',
                           client_name='Cliente', loan_number='000123-1', currency='USD', amount=20)
        _, base = core.build_views([period], [], [application])
        associated = dict(deepcopy(base[0]), _pending_receipt=True, source_document='DEP',
                          application_usd=0, deposit_usd=10, open_usd=0)
        unassociated = dict(deepcopy(associated), name=None, source_document='OTHER-DEP',
                            portfolio_credit_status='Sin período asociado')
        with patch.object(report, 'records', return_value=[period]), \
                patch.object(report, 'child_records', return_value=[]), \
                patch.object(report, 'load_direct_applications', return_value=[application]), \
                patch.object(report, 'load_pending_detail', return_value=[associated, unassociated]), \
                patch.object(frappe, 'has_permission', return_value=True), \
                patch.object(core, 'records', return_value=[dict(name='CUT', report_date='2026-05-31')]), \
                patch.object(core, 'get_credit_rows', return_value=[dict(
                    credit_number='123-1', credit_status='Activo', employer='E')]) as query:
            columns, rows = report.execute({'view': 'Detalle'})[:2]
        query.assert_called_once()
        self.assertEqual(query.call_args.kwargs['loans'], {'123-1'})
        self.assertEqual([row['portfolio_credit_status'] for row in rows], ['Activo', 'Activo', 'Sin período asociado'])
        self.assertIs(rows[1], associated)
        self.assertIs(rows[2], unassociated)
        self.assertEqual(columns, report.get_columns(True))

    def test_distinct_dependencies_are_complete_across_parent_pages(self):
        population = [frappe._dict(parent=f'IMPORT-{i:04}', name=f'IMPORT-{i:04}') for i in range(501)]
        scope = {'historical_period': ['in', ['P']], 'event_type': 'Aplicacion', 'effective': 1,
                 'match_status': 'Conciliado'}

        def pages(doctype, **kwargs):
            self.assertEqual(doctype, 'CN Source Row')
            self.assertEqual(kwargs['group_by'], 'parent')
            self.assertEqual(kwargs['fields'], ['parent', 'parent as name'])
            self.assertEqual(kwargs['filters'], {**scope, 'parenttype': 'CN Accounting Import', 'parentfield': 'rows'})
            start = kwargs['limit_start']
            return population[start:start + kwargs['limit_page_length']]

        with patch.object(frappe, 'get_all', side_effect=pages) as query:
            result = list(core.records_for_dependencies(scope))
        self.assertEqual(result, population)
        self.assertEqual(query.call_count, 2)
