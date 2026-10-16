import json
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.reconciliation_report import add_portfolio_status, build_views, load_direct_applications, AMOUNTS
from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion import resumen_de_conciliacion as report


def period(name='P', **kwargs):
    return dict(name=name, employer='E', payroll_month='2026-05-01', reconciliation_mode='Operativa',
                usd_currency='USD', closure_state='Abierto', **kwargs)


def collection(name='C', **kwargs):
    return dict(name=name, parent='P', client='2885', client_name='Roger', loan_number='0011125-1',
                **kwargs)


def application(name='A', **kwargs):
    return dict(name=name, event_type='Aplicacion', historical_period='P', client='2885', client_name='Roger',
                loan_number='11125-1', currency='USD', **kwargs)


class ReconciliationReportTests(unittest.TestCase):
    def test_direct_projection_not_counted_twice_and_same_totals_in_both_views(self):
        claims = [collection(expected_usd=100, applied_usd=100, remitted_usd=90,
            remittance_detail=json.dumps([
                {'importe_usd': 60, 'destino': 'Cobranza'},
                {'importe_usd': 20, 'destino': 'Cobranza', 'aplicacion_directa': 'A'},
                {'importe_usd': 10, 'destino': 'Partida complementaria'},
            ]))]
        apps = [application(amount=40, historical_remitted_usd=20,
            historical_detail='[{"importe_usd":20}]',
            application_allocation_detail='[{"collection_row_id":"C","amount_usd":40}]')]
        summary, detail = build_views([period(applied_usd=100)], claims, apps)
        self.assertEqual(len(detail), 1)
        row = detail[0]
        self.assertEqual((row['name'], row['employer'], row['client_number'], row['loan_number']), ('P', 'E', '2885', '11125-1'))
        self.assertEqual(row['collection_usd'], 100)
        self.assertEqual(row['application_usd'], 100)
        self.assertEqual(row['deposit_usd'], 80)
        self.assertEqual(row['collection_difference_usd'], 0)
        self.assertEqual(row['deposit_difference_usd'], 20)
        self.assertEqual(row['open_usd'], 20)
        for field in AMOUNTS:
            self.assertEqual(summary[0][field], row[field])

    def test_credit_usage_and_signed_adjustment_do_not_become_deposit(self):
        apps = [application(amount=100, historical_remitted_usd=99.99,
            historical_detail=json.dumps([{'importe_usd': 79.23},
                {'importe_usd': 20.76, 'saldo_a_favor': 'COMP'},
                {'importe_usd': 0, 'diferencia_usd': -.01}]))]
        for extra in [dict(no_collection=1), dict()]:
            p = period(applied_usd=100, **extra)
            if not extra:
                p['reconciliation_mode'] = 'Historica'
            summary, detail = build_views([p], [], apps)
            for row in [summary[0], detail[0]]:
                self.assertIsNone(row['collection_usd'])
                self.assertIsNone(row['collection_difference_usd'])
                self.assertEqual(row['collection_control'], 'No aplica')
                self.assertEqual(row['deposit_usd'], 79.23)
                self.assertEqual(row['deposit_difference_usd'], 20.77)
                self.assertEqual(row['credit_used_usd'], 20.76)
                self.assertEqual(row['cash_adjustment_usd'], -.01)
                self.assertEqual(row['open_usd'], 0)
                self.assertEqual(row['cash_control'], 'Cubierta')

    def test_offsets_cannot_hide_differences_or_clear_pending(self):
        claims = [collection(expected_usd=100, applied_usd=80, remitted_usd=100,
                             remittance_detail='[{"importe_usd":100}]'),
                  dict(collection('C2', expected_usd=100, applied_usd=120, remitted_usd=100,
                             remittance_detail='[{"importe_usd":100}]'), client='2', loan_number='2-1')]
        summary, detail = build_views([period(applied_usd=200)], claims, [])
        row = summary[0]
        self.assertEqual(row['collection_difference_usd'], 0)
        self.assertEqual(row['deposit_difference_usd'], 0)
        self.assertEqual(row['difference_count'], 2)
        self.assertEqual(row['collection_control'], 'Con diferencias')
        self.assertEqual(row['open_usd'], 20)
        self.assertTrue(row['has_difference'])
        self.assertEqual(sum(r['open_usd'] for r in detail), 20)

    def test_same_credit_in_different_periods_or_cycles_stays_separate(self):
        claims = [collection(expected_usd=10, applied_usd=0),
                  dict(collection('C2', expected_usd=5), parent='P2'),
                  dict(collection('C3', expected_usd=8), loan_number='11125-2')]
        summary, detail = build_views([period(), period('P2')], claims, [])
        self.assertEqual(len(detail), 3)
        self.assertEqual([r['credit_count'] for r in summary], [2, 1])
        self.assertEqual([r['collection_usd'] for r in summary], [18, 5])

    def test_missing_conversion_or_corrupt_distribution_is_not_zero(self):
        claims = [collection(expected_usd=0, expected_nio=100, applied_usd=5,
                             remitted_usd=5, remittance_detail='broken')]
        summary, detail = build_views([period(applied_usd=5)], claims, [])
        for row in [summary[0], detail[0]]:
            self.assertIsNone(row['collection_usd'])
            self.assertIsNone(row['deposit_usd'])
            self.assertIsNone(row['open_usd'])
            self.assertEqual(row['cash_control'], 'Revisar')
            self.assertTrue(row['observations'])

    def test_net_application_uses_confirmed_application_adjustment(self):
        apps = [application(amount=100, application_adjustment_usd=10,
                            historical_remitted_usd=90, historical_detail='[{"importe_usd":90}]')]
        summary, detail = build_views([period(applied_usd=90, no_collection=1)], [], apps)
        self.assertEqual(detail[0]['application_usd'], 90)
        self.assertEqual(summary[0]['deposit_difference_usd'], 0)
        self.assertEqual(summary[0]['open_usd'], 0)
        self.assertFalse(summary[0]['observations'])

    def test_missing_breakdown_is_not_reported_as_zero_application(self):
        summary, detail = build_views([period(applied_usd=100)], [], [])
        self.assertIsNone(summary[0]['application_usd'])
        self.assertIsNone(summary[0]['open_usd'])
        self.assertTrue(summary[0]['observations'])
        self.assertEqual(detail, [])

    def test_empty_regular_period_has_not_passed_collection_control(self):
        summary, _ = build_views([period()], [], [])
        self.assertEqual(summary[0]['collection_control'], 'Sin cobranza')
        self.assertTrue(summary[0]['has_difference'])

    def test_projection_of_adjustment_and_credit_counted_once(self):
        evidence = [{'importe_usd': 9.99, 'saldo_a_favor': 'COMP', 'aplicacion_directa': 'A'},
                    {'importe_usd': 0, 'diferencia_usd': -.01, 'aplicacion_directa': 'A'}]
        claims = [collection(expected_usd=10, applied_usd=10, remitted_usd=9.99,
            rounding_adjustment_usd=-.01, remittance_detail=json.dumps(evidence))]
        apps = [application(amount=10, collection_row_id='C', historical_remitted_usd=9.99,
            historical_detail=json.dumps([{k: v for k, v in e.items() if k != 'aplicacion_directa'} for e in evidence]))]
        summary, detail = build_views([period(applied_usd=10)], claims, apps)
        for row in [summary[0], detail[0]]:
            self.assertEqual(row['application_usd'], 10)
            self.assertEqual(row['credit_used_usd'], 9.99)
            self.assertEqual(row['cash_adjustment_usd'], -.01)
            self.assertEqual(row['open_usd'], 0)

    def test_inaccessible_direct_import_fails_without_returning_amounts(self):
        with patch('credinomina_reconciliation.reconciliation_report.records_for_dependencies',
                   return_value=[frappe._dict(parent='PRIVATE')]), \
             patch.object(frappe, 'get_list', return_value=[]), \
             patch.object(frappe, 'get_all', return_value=[frappe._dict(name='PRIVATE')]), \
             patch.object(frappe, 'throw', side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                load_direct_applications(['P'])

    def test_both_column_sets_show_two_comparisons_and_detail_identifiers(self):
        required = {'name', 'employer', 'collection_usd', 'application_usd', 'deposit_usd',
                    'collection_difference_usd', 'deposit_difference_usd', 'open_usd'}
        for detail in [False, True]:
            fields = {c['fieldname'] for c in report.get_columns(detail)}
            self.assertTrue(required <= fields)
            if detail:
                self.assertTrue({'client_number', 'client_name', 'loan_number', 'first_exception_comment',
                                 'comments', 'application_comment', 'portfolio_credit_status'} <= fields)
            else:
                self.assertNotIn('first_exception_comment', fields)

    def test_first_exception_comments_preserved_without_repetition_per_loan(self):
        claims = [collection('C1', expected_usd=10, first_exception_comment='No enviado a cobrar'),
                  collection('C2', expected_usd=20, first_exception_comment='No enviado a cobrar'),
                  collection('C3', expected_usd=5, first_exception_comment='Revisar cuota')]
        _, detail = build_views([period()], claims, [])
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]['first_exception_comment'], 'No enviado a cobrar\nRevisar cuota')

    def test_collection_comments_preserved_per_credit_and_period(self):
        claims = [collection('C1', comments=' Nota ', application_comment='Aplicado'),
                  collection('C2', comments='Nota', application_comment='Revisar'),
                  dict(collection('C3', comments='Otro crédito'), loan_number='11125-2'),
                  dict(collection('C4', comments='Otro período'), parent='P2')]
        _, detail = build_views([period(), period('P2')], claims, [])
        rows = {(r['name'], r['loan_number']): r for r in detail}
        self.assertEqual(rows['P', '11125-1']['comments'], 'Nota')
        self.assertEqual(rows['P', '11125-1']['application_comment'], 'Aplicado\nRevisar')
        self.assertEqual(rows['P', '11125-2']['comments'], 'Otro crédito')
        self.assertEqual(rows['P2', '11125-1']['comments'], 'Otro período')
        _, direct_only = build_views([period()], [], [application(amount=10)])
        self.assertEqual(direct_only[0]['comments'], '')
        self.assertEqual(direct_only[0]['application_comment'], '')

    def test_portfolio_status_uses_period_cutoff_month_and_previous_available_cut(self):
        periods = [period(cutoff_date='2026-06-15'), period('P2')]
        _, detail = build_views(periods, [collection(), dict(collection('C2'), parent='P2')], [])
        cuts = [dict(name='JUN', report_date='2026-06-30'), dict(name='APR', report_date='2026-04-30')]
        def credits(doctype, **kwargs):
            status = 'Vencido' if kwargs['filters']['parent'] == 'JUN' else 'Corriente'
            return [dict(credit_number='0011125-1', credit_status=status, employer='E')]
        with patch('credinomina_reconciliation.reconciliation_report.frappe.has_permission', return_value=True), \
             patch('credinomina_reconciliation.reconciliation_report.records', return_value=cuts) as query, \
             patch('credinomina_reconciliation.reconciliation_report.get_credit_rows', side_effect=credits) as lookup:
            add_portfolio_status(periods, detail)
        self.assertEqual([r['portfolio_credit_status'] for r in detail], ['Vencido', 'Corriente'])
        self.assertEqual(str(query.call_args.kwargs['filters']['report_date'][1]), '2026-06-30')
        self.assertEqual(query.call_args.kwargs['filters']['disabled'], 0)
        self.assertEqual(query.call_args.kwargs['filters']['status'], ['in', ['Importado', 'Importado con alertas']])
        self.assertEqual(lookup.call_count, 2)

    def test_portfolio_status_does_not_guess_missing_duplicate_or_wrong_company(self):
        for credits, expected in (
            ([], 'Crédito no encontrado en corte'),
            ([dict(credit_number='11125-2', credit_status='Cancelado', employer='E')], 'Crédito no encontrado en corte'),
            ([dict(credit_number='11125-1', credit_status='Cancelado', employer='OTHER')], 'Empresa distinta en corte'),
            ([dict(credit_number='11125-1', credit_status='Corriente', employer='E')] * 2, 'Crédito duplicado en corte'),
        ):
            with self.subTest(expected=expected):
                _, detail = build_views([period()], [collection()], [])
                with patch('credinomina_reconciliation.reconciliation_report.frappe.has_permission', return_value=True), \
                     patch('credinomina_reconciliation.reconciliation_report.records', return_value=[dict(name='CUT', report_date='2026-05-31')]), \
                     patch('credinomina_reconciliation.reconciliation_report.get_credit_rows', return_value=credits):
                    add_portfolio_status([period()], detail)
                self.assertEqual(detail[0]['portfolio_credit_status'], expected)

    def test_portfolio_status_respects_permissions_and_absent_cuts(self):
        for permission in (False, True):
            _, detail = build_views([period()], [collection()], [])
            with patch('credinomina_reconciliation.reconciliation_report.frappe.has_permission', return_value=permission), \
                 patch('credinomina_reconciliation.reconciliation_report.records', return_value=[]) as query, \
                 patch('credinomina_reconciliation.reconciliation_report.get_credit_rows') as lookup:
                add_portfolio_status([period()], detail)
            self.assertEqual(detail[0]['portfolio_credit_status'], 'Sin corte de cartera aplicable')
            if not permission:
                query.assert_not_called()
            lookup.assert_not_called()

    def test_execute_switches_granularity_and_filters_differences(self):
        p = frappe._dict(period(applied_usd=20))
        claims = [frappe._dict(collection(expected_usd=20, applied_usd=20, remitted_usd=20,
                                        remittance_detail='[{"importe_usd":20}]'))]
        with patch.object(report, 'records', side_effect=lambda doctype, **kw: [p] if doctype == 'CN Reconciliation Period' else []), \
             patch.object(report, 'child_records', return_value=claims) as child_query, \
             patch.object(report, 'load_direct_applications', return_value=[]), \
             patch.object(report, 'add_portfolio_status') as enrich:
            _, summary = report.execute()[:2]
            _, detail = report.execute({'view': 'Detalle'})[:2]
            _, filtered = report.execute({'view': 'Detalle', 'only_differences': 1})[:2]
        self.assertEqual(enrich.call_count, 2)
        self.assertTrue({'comments', 'application_comment'} <= set(child_query.call_args.kwargs['fields']))
        self.assertNotIn('client_number', summary[0])
        self.assertEqual(detail[0]['client_number'], '2885')
        self.assertEqual(filtered, [])

    def test_unknown_amounts_disable_automatic_totals(self):
        p = frappe._dict(period(applied_usd=100))
        with patch.object(report, 'records', return_value=[p]), \
             patch.object(report, 'child_records', return_value=[]), \
             patch.object(report, 'load_direct_applications', return_value=[]):
            result = report.execute()
        self.assertTrue(result[5])
        self.assertIn('Totales omitidos', result[2])
        self.assertIsNone(result[1][0]['open_usd'])
