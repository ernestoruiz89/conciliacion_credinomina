import copy
import unittest
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation.historical_cutoff import reconstruct, load_cutoff
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as statement
from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos import antiguedad_de_saldos as aging


@pytest.fixture(autouse=True)
def no_documented_deposit_differences(monkeypatch):
    monkeypatch.setattr(statement, 'load_deposit_differences', lambda filters: [])


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

    def test_split_adjustment_against_application_uses_effective_date(self):
        item = self.item(category='Compensación entre partidas', amount_usd=10,
            source_credit=10, source_debit=0,
            reclassification_allocations=[dict(operation_id='OP', kind='Pago pendiente contra aplicación',
                target='A', parent_document='I', employer='E', amount_usd=10, effective_date='2025-05-01')])
        before = self.cut('2025-04-30', deposits=[], items=[item])
        self.assertEqual(before['applications'][0]['amount_usd'], 100)
        self.assertEqual(before['summary'][0]['core_pending_usd'], -10)
        after = self.cut('2025-05-01', deposits=[], items=[item])
        self.assertEqual(after['applications'][0]['amount_usd'], 90)
        self.assertEqual(after['summary'][0]['core_pending_usd'], 0)

    def test_one_adjustment_distributed_between_applications_is_counted_once_at_cutoff(self):
        self.source.update(amount=.17, application_adjustment_usd=.17)
        self.context[0].append(dict(self.source, name='B', amount=.47, application_adjustment_usd=.47))
        item = self.item(category='Ajuste de aplicación', posting_date='2025-05-01', amount_usd=.64,
            related_application='A', application_adjustment_usd=.17, source_credit=.64, source_debit=0,
            application_adjustment_history='[{"related_application":"B","application_adjustment_usd":0.47}]')
        before = self.cut(items=[item], deposits=[])
        self.assertEqual(sum(row['amount_usd'] for row in before['applications']), .64)
        after = self.cut('2025-05-01', items=[item], deposits=[])
        self.assertEqual(sum(row['amount_usd'] for row in after['applications']), 0)
        self.assertEqual(sum(row.get('balance_usd') or 0 for row in after['summary']), 0)

    def test_settled_manual_item_pending_registration_appears_only_after_its_origin_date(self):
        item = self.item(name='MANUAL', posting_date='2025-05-01', category='Compensación entre partidas',
                         accounting_source_key='', accounting_status='Pendiente de registro',
                         compensated_usd=20)
        offsets = [dict(parent='MANUAL', compensation_date='2025-05-01', amount_usd=20)]
        before = self.cut('2025-04-30', deposits=[], items=[item], offsets=offsets)
        after = self.cut('2025-05-01', deposits=[], items=[item], offsets=offsets)
        self.assertEqual(before['summary'][0]['accounting_pending_count'], 0)
        self.assertEqual(after['summary'][0]['accounting_pending_count'], 1)
        control = next(row for row in after['detail'] if row['source_document'] == 'MANUAL')
        self.assertEqual((control['balance_usd'], control['accounting_status']),
                         (0, 'Pendiente de registro'))

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

    def test_core_reclassification_separates_credit_origin_and_reconciliation_dates(self):
        empty_context = ([], {}, {}, {}, {})
        deposit = self.deposit(amount_usd=20, deposit_date='2025-04-30', allocation_detail=[])
        item = self.item(category='Saldo a favor del cliente', result='Saldo a favor documentado',
            posting_date='2025-06-01', source_date='2025-06-01', credit_origin_date='2025-04-30',
            reclassification_date='2025-06-01', registered_deposit='D', credit_resolved_usd=0, credit_history='[]')
        before = self.cut('2025-04-30', [deposit], [item], context=empty_context)['summary'][0]
        self.assertEqual((before['client_credit_usd'], before['core_pending_usd'], before['deposit_pending_usd']), (-20, 0, 0))
        before_detail = self.cut('2025-04-30', [deposit], [item], context=empty_context)['detail']
        self.assertEqual(next(row['status'] for row in before_detail if row['source_document'] == 'C'), 'Pendiente de reclasificación')
        after = self.cut('2025-06-01', [deposit], [item], context=empty_context)['summary'][0]
        self.assertEqual((after['client_credit_usd'], after['core_pending_usd'], after['deposit_pending_usd']), (-20, 0, 0))

        item.update(posting_date='2025-04-01', source_date='2025-04-01', credit_origin_date='2025-04-30',
                    reclassification_date='2025-04-30')
        before = self.cut('2025-04-15', [deposit], [item], context=empty_context)['summary'][0]
        self.assertEqual((before['client_credit_usd'], before['core_pending_usd']), (0, 20))
        after = self.cut('2025-04-30', [deposit], [item], context=empty_context)['summary'][0]
        self.assertEqual((after['client_credit_usd'], after['core_pending_usd']), (-20, 0))

    def test_split_reclassification_keeps_customer_company_income_and_other_separate(self):
        empty_context = ([], {}, {}, {}, {})
        kinds = ['Saldo a favor contra depósito', 'Saldo a favor de la empresa contra depósito',
                 'Ingreso contra depósito', 'Otro sin cuenta por cobrar contra depósito']
        journal = [dict(operation_id=str(i), kind=kind, target=f'R{i}', parent_document='D', employer='E',
                        client='CLIENT', client_name='Cliente', credit_origin_date='2025-04-30',
                        effective_date='2025-06-01', amount_usd=.01) for i, kind in enumerate(kinds)]
        item = self.item(category='Compensación entre partidas', amount_usd=.10, source_debit=.10,
            source_currency='USD', posting_date='2025-06-01', source_date='2025-06-01',
            reclassification_allocations=journal)
        deposit = self.deposit(amount_usd=.04, deposit_date='2025-04-30', allocation_detail=[
            dict(tipo='Partida complementaria', partida='C', importe_usd=.01),
            dict(tipo='Partida complementaria', partida='C', importe_usd=.01)])
        before = self.cut('2025-04-30', [deposit], [item], context=empty_context)['summary'][0]
        self.assertEqual((before['client_credit_usd'], before['company_credit_usd'], before['deposit_pending_usd']),
                         (-.01, -.01, -.02))
        after = self.cut('2025-06-01', [deposit], [item], context=empty_context)['summary'][0]
        self.assertEqual((after['client_credit_usd'], after['company_credit_usd'], after['deposit_pending_usd'],
                          after['core_pending_usd']), (-.01, -.01, 0, .06))

    def test_credit_application_plus_corrected_compensation_exhausts_balance_at_cutoff(self):
        deposit = self.deposit(amount_usd=32.63, deposit_date='2025-04-30', allocation_detail=[])
        item = self.item(category='Saldo a favor del cliente', result='Saldo a favor documentado',
            amount_usd=32.63, accounting_source_key='', registered_deposit='D', compensated_usd=18.46,
            credit_resolved_usd=32.63, credit_history=[dict(fecha='2025-05-15', importe_usd=14.17)])
        offsets = [dict(parent='C', compensation_date=day, amount_usd=amount) for day, amount in [
            ('2026-10-07', 18.46), ('2026-10-15', -18.46), ('2026-10-07', -18.46),
            ('2026-10-15', 18.46), ('2025-06-01', 18.46)]]
        for day, pending in [('2025-05-14', -32.63), ('2025-05-15', -18.46),
                             ('2025-06-01', 0), ('2026-08-31', 0), ('2026-10-07', 0), ('2026-10-15', 0)]:
            with self.subTest(day=day):
                result = self.cut(day, [deposit], [item], offsets)
                self.assertEqual(result['summary'][0]['client_credit_usd'], pending)
        offsets.append(dict(parent='C', compensation_date='2026-10-16', amount_usd=-18.46))
        item.update(compensated_usd=0, credit_resolved_usd=14.17)
        for day, pending in [('2026-08-31', 0), ('2026-10-16', -18.46)]:
            self.assertEqual(self.cut(day, [deposit], [item], offsets)['summary'][0]['client_credit_usd'], pending)

    def test_credit_application_and_reversal_cover_only_after_their_dates(self):
        deposit = self.deposit(amount_usd=20, deposit_date='2025-04-30', allocation_detail=[])
        item = self.item(category='Saldo a favor del cliente', result='Saldo a favor documentado',
            accounting_source_key='', registered_deposit='D', credit_resolved_usd=0, credit_history=[
                dict(operation_id='USE', application_id='A', fecha='2025-05-01', importe_usd=19.99),
                dict(operation_id='REV', reverses='USE', application_id='A', fecha='2025-06-01', importe_usd=-19.99)])
        for day, application_pending, credit_pending in [('2025-04-30', 100, -20),
                ('2025-05-01', 80.01, -.01), ('2025-06-01', 100, -20)]:
            with self.subTest(day=day):
                result = self.cut(day, [deposit], [item])
                self.assertEqual(result['summary'][0]['application_pending_usd'], application_pending)
                self.assertEqual(result['summary'][0]['client_credit_usd'], credit_pending)
                self.assertEqual(result['summary'][0]['deposit_pending_usd'], 0)

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
        with patch.object(statement, 'load_core_detail', return_value=[]), \
                patch('credinomina_reconciliation.historical_cutoff.load_cutoff', return_value=result), patch(
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
        with patch.object(statement, 'load_core_detail', return_value=[]), \
                patch('credinomina_reconciliation.historical_cutoff.load_cutoff', return_value=result), patch(
                'credinomina_reconciliation.historical_cutoff.cutoff_message', return_value='Corte'):
            summary = statement.execute({'cutoff_date': '2025-04-30'})[1]
            detail = statement.execute({'cutoff_date': '2025-04-30', 'view_mode': 'Detalle'})[1]
        movements = [row for row in detail if not row.get('_is_statement_total')]
        self.assertEqual(sum(row['application_pending_usd'] for row in movements), summary[0]['application_pending_usd'])
        self.assertEqual(sum(row['core_balance_usd'] for row in movements), summary[0]['core_balance_usd'])
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
