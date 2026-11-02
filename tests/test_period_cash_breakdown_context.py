import copy
import json
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.period_totals import PeriodTotalsContext


class PeriodCashBreakdownContextTests(unittest.TestCase):
    def period(self, name='P', **values):
        return frappe._dict(name=name, employer='E', reconciliation_mode='Historica', applied_usd=100,
                            status='Conciliado', collection_rows=[], **values)

    def source(self, name='A', period='P', amount=100):
        return frappe._dict(name=name, historical_period=period, event_type='Aplicacion', effective=1,
            match_status='Conciliado', currency='USD', amount=amount, historical_remitted_usd=amount)

    def allocation(self, claim='H:A', amount=100, deposit='D'):
        return dict(deposit_id=deposit, claim_id=claim, amount_usd=amount)

    def credit(self, amount, operation='OP'):
        return dict(importe_usd=amount, origen='Saldo a favor', saldo_a_favor='CREDIT',
                    operacion=operation, deposito_origen='ORIGIN')

    def values(self, periods, sources, ledger):
        with patch.object(frappe, 'get_all', return_value=[]):
            context = PeriodTotalsContext(periods, sources, cash_allocation={'allocations': ledger})
            return [context.values(period) for period in periods]

    def test_current_not_yet_saved_journal_splits_cash_and_preserves_financial_totals(self):
        period, source = self.period(), self.source()
        ledger = [self.allocation(), self.allocation('X:N', -10)]
        before = copy.deepcopy((period, source, ledger))
        values = self.values([period], [source], ledger)[0]
        self.assertEqual(values['deposit_coverage_usd'], 90)
        self.assertEqual(values['complementary_coverage_usd'], 10)
        self.assertEqual(values['negative_complementary_usd'], -10)
        self.assertEqual(values['cash_breakdown_known'], 1)
        self.assertEqual((values['applied_total_usd'], values['remitted_total_usd'], values['pending_usd']), (100, 100, 0))
        self.assertEqual((period, source, ledger), before)

    def test_one_period_aggregate_does_not_invent_per_application_amounts(self):
        sources = [self.source('A', amount=60), self.source('B', amount=40)]
        values = self.values([self.period()], sources,
            [self.allocation('H:A', 60), self.allocation('H:B', 40), self.allocation('X:N', -10)])[0]
        self.assertEqual(values['deposit_coverage_usd'], 90)
        self.assertEqual([row.historical_remitted_usd for row in sources], [60, 40])

    def test_credit_funding_is_complementary_coverage_without_new_cash(self):
        source = self.source()
        source.historical_detail = [self.credit(100)]
        values = self.values([self.period()], [source],
                             [self.allocation(deposit='credit:OP')])[0]
        self.assertEqual(values['deposit_coverage_usd'], 0)
        self.assertEqual(values['complementary_coverage_usd'], 100)
        self.assertEqual(values['negative_complementary_usd'], 0)
        self.assertEqual((values['remitted_total_usd'], values['pending_usd']), (100, 0))

    def test_credit_and_negative_complementary_are_distinct_from_bank_cash(self):
        period, source = self.period(), self.source(amount=105)
        period.applied_usd = 105
        source.historical_detail = [dict(importe_usd=100, origen='Banco'), self.credit(5)]
        values = self.values([period], [source], [self.allocation(amount=100),
            self.allocation('X:N', -10), self.allocation(amount=5, deposit='credit:OP')])[0]
        self.assertEqual(values['deposit_coverage_usd'], 90)
        self.assertEqual(values['complementary_coverage_usd'], 15)
        self.assertEqual(values['negative_complementary_usd'], -10)
        self.assertEqual((values['applied_total_usd'], values['remitted_total_usd'], values['pending_usd']), (105, 105, 0))

    def test_operational_credit_is_not_counted_again_in_collection_projection(self):
        period, source = self.period(), self.source()
        period.reconciliation_mode = 'Operativa'
        source.application_allocation_detail = [dict(collection_row_id='ROW', amount_usd=100)]
        source.historical_detail = [dict(importe_usd=90, origen='Banco'), self.credit(10)]
        period.collection_rows = [frappe._dict(name='ROW', applied_usd=100, remitted_usd=100,
            remittance_detail=[dict(importe_usd=90, destino='Cobranza', aplicacion_directa='A'),
                               dict(self.credit(10), destino='Cobranza', aplicacion_directa='A')])]
        before = copy.deepcopy((period, source))
        values = self.values([period], [source], [self.allocation(amount=90),
            self.allocation(amount=10, deposit='credit:OP')])[0]
        self.assertEqual(values['deposit_coverage_usd'], 90)
        self.assertEqual(values['complementary_coverage_usd'], 10)
        self.assertEqual((values['remitted_total_usd'], values['remitted_usd'], values['pending_usd']), (100, 100, 0))
        self.assertEqual((period, source), before)

    def test_incomplete_funding_evidence_hides_only_cash_components(self):
        for detail in ([dict(importe_usd=100, origen='Saldo a favor')],
                       [self.credit(90)],
                       [dict(self.credit(100), operacion=None)]):
            with self.subTest(detail=detail):
                source = self.source()
                source.historical_detail = detail
                values = self.values([self.period()], [source], [])[0]
                self.assertFalse(values['cash_breakdown_known'])
                self.assertIsNone(values['deposit_coverage_usd'])
                self.assertEqual((values['remitted_total_usd'], values['pending_usd']), (100, 0))
                self.assertIn('saldo a favor', values['cash_breakdown_note'])

    def test_duplicate_credit_operation_cannot_create_additional_cash_correction(self):
        sources = [self.source('A', amount=50), self.source('B', amount=50)]
        for source in sources:
            source.historical_detail = [dict(importe_usd=40, origen='Banco'), self.credit(10)]
        values = self.values([self.period()], sources, [])[0]
        self.assertFalse(values['cash_breakdown_known'])
        self.assertEqual((values['remitted_total_usd'], values['pending_usd']), (100, 0))

    def test_shared_periods_hide_only_unattributable_components(self):
        periods = [self.period('P1'), self.period('P2')]
        sources = [self.source('A', 'P1', 60), self.source('B', 'P2', 40)]
        values = self.values(periods, sources,
            [self.allocation('H:A', 60), self.allocation('H:B', 40), self.allocation('X:N', -10)])
        for result, gross in zip(values, (60, 40)):
            self.assertEqual(result['cash_breakdown_known'], 0)
            self.assertIsNone(result['deposit_coverage_usd'])
            self.assertIsNone(result['negative_complementary_usd'])
            self.assertEqual(result['remitted_total_usd'], gross)
            self.assertEqual(result['pending_usd'], 0)
            self.assertIn('varios períodos', result['cash_breakdown_note'])

    def test_positive_fee_and_negative_offset_do_not_guess_loan_cash(self):
        result = self.values([self.period()], [self.source()],
            [self.allocation(), self.allocation('X:FEE', 5), self.allocation('X:N', -10)])[0]
        self.assertFalse(result['cash_breakdown_known'])
        self.assertIsNone(result['deposit_coverage_usd'])
        self.assertEqual(result['pending_usd'], 0)

    def test_invalid_current_amount_marks_breakdown_unknown_without_losing_coverage(self):
        for amount in ('not-an-amount', 'NaN'):
            with self.subTest(amount=amount):
                result = self.values([self.period()], [self.source()], [self.allocation(amount=amount)])[0]
                self.assertFalse(result['cash_breakdown_known'])
                self.assertEqual((result['remitted_total_usd'], result['pending_usd']), (100, 0))

    def test_refresh_reads_the_complete_saved_deposit(self):
        deposit = frappe._dict(name='D', allocation_detail=json.dumps([
            dict(tipo='Aplicacion historica', periodo='P', aplicacion_id='A', importe_usd=100),
            dict(tipo='Partida complementaria', partida='N', importe_usd=-10)]))
        def read(doctype, **kwargs):
            return [deposit] if doctype == 'CN Remittance Allocation' else []
        with patch.object(frappe, 'get_all', side_effect=read):
            context = PeriodTotalsContext([self.period()], [self.source()])
            result = context.values(self.period())
        self.assertEqual(result['deposit_coverage_usd'], 90)
        self.assertEqual(result['negative_complementary_usd'], -10)

    def test_invalid_saved_journal_is_not_treated_as_zero_offset(self):
        deposit = frappe._dict(name='D', allocation_detail='invalid P journal')
        with patch.object(frappe, 'get_all', side_effect=lambda doctype, **kwargs: [deposit] if doctype == 'CN Remittance Allocation' else []):
            result = PeriodTotalsContext([self.period()], [self.source()]).values(self.period())
        self.assertFalse(result['cash_breakdown_known'])
        self.assertIsNone(result['deposit_coverage_usd'])

    def test_invalid_other_journal_does_not_match_partial_record_names(self):
        for raw in ('invalid P10 journal', 'invalid A10 journal', 'invalid P1-OTHER journal'):
            with self.subTest(raw=raw):
                deposit = frappe._dict(name='D', allocation_detail=raw)
                with patch.object(frappe, 'get_all', side_effect=lambda doctype, **kwargs:
                        [deposit] if doctype == 'CN Remittance Allocation' else []):
                    period = self.period('P1')
                    result = PeriodTotalsContext([period], [self.source('A1', 'P1')]).values(period)
                self.assertTrue(result['cash_breakdown_known'])
                self.assertEqual(result['deposit_coverage_usd'], 100)

    def test_other_period_is_resolved_before_classifying_a_shared_saved_deposit(self):
        deposit = frappe._dict(name='D', allocation_detail=json.dumps([
            dict(tipo='Aplicacion historica', periodo='P', aplicacion_id='A', importe_usd=60),
            dict(tipo='Aplicacion historica', periodo='OTHER', aplicacion_id='B', importe_usd=40),
            dict(tipo='Partida complementaria', partida='N', importe_usd=-10)]))
        def read(doctype, **kwargs):
            if doctype == 'CN Remittance Allocation':
                return [deposit]
            if doctype == 'CN Source Row':
                return [self.source('B', 'OTHER', 40)]
            return []
        with patch.object(frappe, 'get_all', side_effect=read):
            result = PeriodTotalsContext([self.period()], [self.source('A', amount=60)]).values(self.period())
        self.assertFalse(result['cash_breakdown_known'])
        self.assertEqual(result['remitted_total_usd'], 60)

    def test_lost_application_in_another_period_does_not_depend_on_batch_size(self):
        deposit = frappe._dict(name='D', allocation_detail=json.dumps([
            dict(tipo='Aplicacion historica', periodo='P1', aplicacion_id='LOST', importe_usd=100),
            dict(tipo='Partida complementaria', partida='N', importe_usd=-10)]))
        first, second = self.period('P1'), self.period('P2')
        sources = [self.source('B', 'P2', 50)]
        def read(doctype, **kwargs):
            return [deposit] if doctype == 'CN Remittance Allocation' else []
        with patch.object(frappe, 'get_all', side_effect=read):
            alone = PeriodTotalsContext([second], sources).values(second)
            together_context = PeriodTotalsContext([first, second], sources)
            together = together_context.values(second)
            missing = together_context.values(first)
        self.assertEqual(alone, together)
        self.assertTrue(together['cash_breakdown_known'])
        self.assertEqual(together['deposit_coverage_usd'], 50)
        self.assertFalse(missing['cash_breakdown_known'])

    def test_unidentified_co_beneficiary_without_period_hint_remains_unknown(self):
        periods = [self.period('P1'), self.period('P2')]
        sources = [self.source('A', 'P1', 60), self.source('B', 'P2', 40)]
        results = self.values(periods, sources,
            [self.allocation('H:A', 60), self.allocation('H:LOST', 40), self.allocation('X:N', -10)])
        self.assertTrue(all(not result['cash_breakdown_known'] for result in results))
