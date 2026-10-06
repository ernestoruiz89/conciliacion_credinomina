import copy
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.historical_cutoff import reconstruct, load_cutoff
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as statement
from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos import antiguedad_de_saldos as aging


class HistoricalCutoffTests(unittest.TestCase):
    def setUp(self):
        self.source = dict(name='A', parent='I', event_type='Aplicacion', effective=1,
            currency='USD', amount=100, event_date='2025-04-30', historical_period='H',
            match_status='Conciliado', historical_remitted_usd=100, client_name='Juan', client_number='1')
        self.context = ([self.source], {'I': dict(name='I', employer='E')},
            {'H': dict(name='H', employer='E', payroll_month='2025-04-01', reconciliation_mode='Historica')},
            {}, {'E': dict(grace_days=10)})

    def deposit(self, **changes):
        return dict(dict(name='D', employer='E', docstatus=1, deposit_date='2025-05-15', amount_usd=100,
            allocation_detail=[dict(tipo='Aplicacion historica', aplicacion_id='A', periodo='H', importe_usd=100)]), **changes)

    def item(self, **changes):
        return dict(dict(name='C', employer='E', docstatus=1, posting_date='2025-04-30',
            category='Por clasificar', amount_usd=20, source_debit=20, accounting_source_key='K'), **changes)

    def cut(self, date='2025-04-30', deposits=None, items=(), offsets=(), context=None, filters=None):
        return reconstruct(context or self.context, [self.deposit()] if deposits is None else deposits,
                           list(items), offsets, date, filters)

    def test_later_deposit_reopens_fully_settled_application_without_mutation(self):
        before = copy.deepcopy(self.context)
        cut = self.cut()
        self.assertEqual(cut['summary'][0]['application_pending_usd'], 100)
        self.assertEqual(cut['summary'][0]['deposit_pending_usd'], 0)
        self.assertEqual(self.cut('2025-05-15')['summary'], [])
        self.assertEqual(self.context, before)

    def test_partial_deposits_by_date_and_advance_unassigned_until_application(self):
        deposits = [self.deposit(name='D1', amount_usd=30, deposit_date='2025-04-20',
            allocation_detail=[dict(tipo='Aplicacion historica', aplicacion_id='A', importe_usd=30)]),
            self.deposit(name='D2', amount_usd=70, allocation_detail=[
                dict(tipo='Aplicacion historica', aplicacion_id='A', importe_usd=70)])]
        self.assertEqual(self.cut('2025-04-20', deposits)['summary'][0]['deposit_pending_usd'], -30)
        self.assertEqual(self.cut('2025-04-30', deposits)['summary'][0]['application_pending_usd'], 70)
        self.assertEqual(self.cut('2025-05-15', deposits)['summary'], [])

    def test_later_application_adjustment_is_not_deducted_early(self):
        self.source['application_adjustment_usd'] = 20
        item = self.item(category='Ajuste de aplicación', posting_date='2025-05-01',
                         related_application='A', application_adjustment_usd=20, source_credit=20, source_debit=0)
        self.assertEqual(self.cut(items=[item], deposits=[])['applications'][0]['amount_usd'], 100)
        cut = self.cut('2025-05-01', items=[item], deposits=[])
        self.assertEqual(cut['applications'][0]['amount_usd'], 80)
        self.assertEqual(cut['summary'][0]['core_pending_usd'], 0)

    def test_compensation_and_reversal_use_their_dates(self):
        item = self.item(category='Compensación entre partidas', amount_usd=100, compensated_usd=25)
        offsets = [dict(parent='C', compensation_date='2025-05-01', amount_usd=40),
                   dict(parent='C', compensation_date='2025-06-01', amount_usd=-15)]
        for day, pending in [('2025-04-30', 100), ('2025-05-01', 60), ('2025-06-01', 75)]:
            self.assertEqual(self.cut(day, [], [item], offsets)['summary'][0]['core_pending_usd'], pending)

    def test_refunds_and_reversals_do_not_release_the_deposit_cash(self):
        deposit = self.deposit(amount_usd=20, deposit_date='2025-04-30', allocation_detail=[])
        item = self.item(category='Saldo a favor del cliente', result='Saldo a favor documentado',
            accounting_source_key='', registered_deposit='D', credit_resolved_usd=15,
            credit_history=[dict(fecha='2025-05-01', importe_usd=20), dict(fecha='2025-06-01', importe_usd=-5)])
        for day, expected in [('2025-04-30', -20), ('2025-05-01', 0), ('2025-06-01', -5)]:
            row = self.cut(day, [deposit], [item])['summary'][0]
            self.assertEqual(row['client_credit_usd'], expected)
            self.assertEqual(row['deposit_pending_usd'], 0)

    def test_credit_classified_later_remains_unassigned_at_cutoff(self):
        deposit = self.deposit(amount_usd=20, deposit_date='2025-04-30', allocation_detail=[])
        item = self.item(category='Saldo a favor de la empresa', result='Saldo a favor documentado',
            posting_date='2025-05-01', accounting_source_key='', registered_deposit='D')
        result = self.cut(deposits=[deposit], items=[item])['summary'][0]
        self.assertEqual((result['deposit_pending_usd'], result['company_credit_usd']), (-20, 0))

    def test_draft_core_deposit_included_manual_draft_and_cancelled_excluded(self):
        deposits = [self.deposit(name='CORE', docstatus=0, accounting_source_key='K'),
            self.deposit(name='MANUAL', docstatus=0), self.deposit(name='VOID', docstatus=2)]
        summary = self.cut('2025-05-31', deposits)['summary'][0]
        self.assertEqual(summary['deposit_pending_usd'], -100)
        self.assertEqual(summary['application_pending_usd'], 100)

    def test_adjustment_receivable_and_later_recovery(self):
        item = self.item(category='Ajuste de conciliación', amount_usd=-20,
                         accounting_source_key='', subcategory_effect='CxC a la empresa')
        dep = self.deposit(deposit_date='2025-04-30', amount_usd=80, allocation_detail=[
            dict(tipo='Aplicacion historica', aplicacion_id='A', importe_usd=100),
            dict(tipo='Partida complementaria', partida='C', empresa='E', importe_usd=-20)])
        receipt = self.item(name='R', category='Cobro de CxC', accounting_source_key='',
                            posting_date='2025-05-30', receivable_origin='C')
        dep2 = self.deposit(name='D2', deposit_date='2025-05-30', amount_usd=20,
            allocation_detail=[dict(tipo='Partida complementaria', partida='R', empresa='E', importe_usd=20)])
        result = self.cut(deposits=[dep, dep2], items=[item, receipt])
        self.assertEqual(result['summary'][0]['application_pending_usd'], 0)
        self.assertEqual(result['summary'][0]['company_receivable_usd'], 20)
        self.assertEqual(self.cut('2025-05-30', [dep, dep2], [item, receipt])['summary'], [])

    def test_missing_future_negative_adjustment_does_not_invent_cash(self):
        item = self.item(category='Ajuste de conciliación', amount_usd=-20,
                         accounting_source_key='', posting_date='2025-05-01')
        dep = self.deposit(deposit_date='2025-04-30', amount_usd=80, allocation_detail=[
            dict(tipo='Aplicacion historica', aplicacion_id='A', importe_usd=100),
            dict(tipo='Partida complementaria', partida='C', importe_usd=-20)])
        result = self.cut(deposits=[dep], items=[item])
        self.assertTrue(result['warnings'])
        self.assertEqual(result['summary'][0]['balance_usd'], 20)
        self.assertEqual(result['summary'][0]['deposit_pending_usd'], -80)

    def test_operational_and_cross_company_deposit(self):
        self.context[2]['H']['reconciliation_mode'] = 'Operativa'
        self.context[3]['COL'] = dict(name='COL', parent='H', row_key='KEY')
        self.source.update(historical_period='', processing_route='Operativa', collection_row_id='COL')
        dep = self.deposit(employer='PAGADORA', deposit_date='2025-04-30', amount_usd=60,
            allocation_detail=[dict(tipo='Cobranza', periodo='H', fila_id='KEY', importe_usd=60)])
        result = self.cut(deposits=[dep], filters={'employer': 'E'})
        self.assertEqual(result['summary'][0]['application_pending_usd'], 40)
        self.assertEqual(result['applications'][0]['paid_usd'], 60)

    def test_mixed_future_operational_sources_warn_not_guessed(self):
        self.context[2]['H']['reconciliation_mode'] = 'Operativa'
        self.context[3]['COL'] = dict(name='COL', parent='H', row_key='KEY')
        self.source.update(historical_period='', processing_route='Operativa', collection_row_id='COL')
        self.context[0].append(dict(self.source, name='FUTURE', event_date='2025-05-30'))
        dep = self.deposit(deposit_date='2025-04-30', allocation_detail=[
            dict(tipo='Cobranza', periodo='H', fila_id='KEY', importe_usd=100)])
        result = self.cut(deposits=[dep])
        self.assertTrue(result['warnings'])
        self.assertEqual(result['summary'][0]['application_pending_usd'], 100)
        self.assertEqual(result['summary'][0]['deposit_pending_usd'], -100)

    def test_invalid_history_fails_instead_of_silent_zero(self):
        with self.assertRaisesRegex(ValueError, 'historial'):
            self.cut(items=[self.item(compensated_usd=20)], deposits=[])
        with self.assertRaisesRegex(ValueError, 'historial ilegible'):
            self.cut('2025-05-31', [self.deposit(allocation_detail='not JSON')])
        with self.assertRaisesRegex(ValueError, 'fecha efectiva'):
            self.cut(items=[self.item(posting_date=None)], deposits=[])

    def test_fifo_proof_separates_operative_applications_across_cutoff(self):
        self.context[2]['H']['reconciliation_mode'] = 'Operativa'
        self.context[3]['COL'] = dict(name='COL', parent='H', row_key='KEY')
        self.source.update(historical_period='', processing_route='Operativa', collection_row_id='COL')
        self.context[0].append(dict(self.source, name='FUTURE', event_date='2025-05-30'))
        dep = self.deposit(deposit_date='2025-04-30', amount_usd=150, allocation_detail=[
            dict(tipo='Cobranza', periodo='H', fila_id='KEY', importe_usd=150, aplicaciones_fifo=[
                dict(application_id='A', amount_usd=100), dict(application_id='FUTURE', amount_usd=50)])])
        result = self.cut(deposits=[dep])
        self.assertFalse(result['warnings'])
        self.assertEqual(result['summary'][0]['application_pending_usd'], 0)
        self.assertEqual(result['summary'][0]['deposit_pending_usd'], -50)
        self.assertEqual(result['applications'][0]['paid_usd'], 100)
        result = self.cut('2025-05-30', [dep])
        self.assertEqual(result['summary'][0]['application_pending_usd'], 50)
        self.assertEqual(result['summary'][0]['deposit_pending_usd'], 0)

    def test_verified_core_evidence_only_suppressed_when_origin_exists_at_cutoff(self):
        core = self.item(registration_exception='EX', _registration_origin='ORIGIN', source_credit=20, source_debit=0)
        origin = self.item(name='ORIGIN', posting_date='2025-05-01', accounting_source_key='')
        result = self.cut(items=[core, origin], deposits=[])
        self.assertEqual(result['summary'][0]['core_pending_usd'], -20)
        self.assertEqual(self.cut('2025-05-01', [], [core, origin])['summary'][0]['core_pending_usd'], 0)

    def test_usd_conversion_and_missing_rate_are_not_zero(self):
        self.source.update(currency='NIO', amount=3662.43, manual_fx_rate=36.6243)
        self.assertEqual(self.cut()['summary'][0]['application_pending_usd'], 100)
        self.source['manual_fx_rate'] = 0
        self.assertIsNone(self.cut()['summary'][0]['balance_usd'])

    def test_report_cutoff_is_explicit_and_exports_its_date(self):
        result = self.cut()
        with patch('credinomina_reconciliation.historical_cutoff.load_cutoff', return_value=result), patch(
                'credinomina_reconciliation.historical_cutoff.cutoff_message', return_value='Corte'):
            columns, rows, message = statement.execute({'cutoff_date': '2025-04-30'})
            self.assertEqual(rows[0]['cutoff_date'], '2025-04-30')
            self.assertIn('cutoff_date', {c['fieldname'] for c in columns})
            output = aging.execute({'historical_cutoff': 1, 'as_of_date': '2025-04-30'})
            self.assertEqual(output[1][0]['amount_usd'], 100)
            self.assertIn('Corte', output[2])

    def test_permission_denied_before_loading_financial_data(self):
        with patch('frappe.has_permission', return_value=False), patch('frappe.throw', side_effect=PermissionError), patch(
                'credinomina_reconciliation.historical_cutoff.records') as reads:
            with self.assertRaises(PermissionError):
                load_cutoff({'cutoff_date': '2025-04-30'})
            reads.assert_not_called()

    def test_tolerance_is_dated_and_preserves_signed_cent(self):
        for applied, cash, delta in [(46.53, 46.52, -.01), (46.52, 46.53, .01)]:
            with self.subTest(delta=delta):
                self.source['amount'] = applied
                item = self.item(category='Diferencia por tolerancia', claim_id='H:A', amount_usd=delta,
                                 accounting_source_key='', posting_date='2025-05-15')
                dep = self.deposit(amount_usd=cash, allocation_detail=[
                    dict(tipo='Aplicacion historica', aplicacion_id='A', importe_usd=min(applied, cash)),
                    dict(tipo='Movimiento de conciliación', movimiento='C', diferencia_usd=delta,
                         importe_usd=max(delta, 0))])
                self.assertEqual(self.cut(items=[item], deposits=[dep])['applications'][0]['amount_usd'], applied)
                self.assertEqual(self.cut('2025-05-15', [dep], [item])['summary'], [])

    def test_native_export_summary_and_detail_agree_with_warning(self):
        result = self.cut()
        result['warnings'] = ['Distribución pendiente al corte']
        with patch('credinomina_reconciliation.historical_cutoff.load_cutoff', return_value=result), patch(
                'credinomina_reconciliation.historical_cutoff.cutoff_message', return_value='Corte'):
            summary = statement.execute({'cutoff_date': '2025-04-30'})[1]
            detail = statement.execute({'cutoff_date': '2025-04-30', 'view_mode': 'Detalle'})[1]
        self.assertEqual(sum(row['balance_usd'] for row in detail), summary[0]['balance_usd'])
        self.assertEqual(summary[0]['cutoff_warning'], detail[0]['cutoff_warning'])

    def test_grouped_aging_preserves_cutoff_metadata(self):
        from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos_por_empresa import antiguedad_de_saldos_por_empresa as grouped
        result = self.cut()
        with patch('credinomina_reconciliation.historical_cutoff.load_cutoff', return_value=result), patch(
                'credinomina_reconciliation.historical_cutoff.cutoff_message', return_value='Corte'):
            columns, rows, *_ = grouped.execute({'historical_cutoff': 1, 'as_of_date': '2025-04-30'})
        self.assertEqual(rows[0]['cutoff_date'], '2025-04-30')
        self.assertEqual(rows[0]['amount_usd'], 100)
        self.assertIn('cutoff_date', {column['fieldname'] for column in columns})

    def test_invalid_cutoff_and_origin_filters_rejected_before_db_reads(self):
        cases = [({}, 'sin fecha'), ({'cutoff_date': '2099-01-01'}, 'futura'),
            ({'cutoff_date': '2025-04-30', 'to_date': '2025-05-01'}, 'Hasta'),
            ({'cutoff_date': '2025-04-30', 'from_date': '2025-04-20', 'to_date': '2025-04-01'}, 'Desde')]
        for filters, expected in cases:
            with self.subTest(filters=filters), patch('frappe.has_permission', return_value=True), patch(
                    'frappe.throw', side_effect=lambda message, *args: (_ for _ in ()).throw(ValueError(message))), patch(
                    'credinomina_reconciliation.historical_cutoff.records') as reads:
                with self.assertRaisesRegex(ValueError, expected):
                    load_cutoff(filters)
                reads.assert_not_called()


if __name__ == '__main__':
    unittest.main()
