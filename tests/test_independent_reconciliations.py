import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.period_totals import calculate_totals
from credinomina_reconciliation.period_closure import validate_direct_applications


class IndependentReconciliationTests(unittest.TestCase):
    def source(self, **values):
        return frappe._dict(dict(name="APP", amount=100, currency="USD", effective=1,
            match_status="Conciliado", quality_status="Conforme según cobranza",
            historical_balance_usd=0, historical_remitted_usd=100,
            deposit_match_status="Depósito conciliado", historical_detail='[{"importe_usd":100}]',
            application_allocation_detail='[{"collection_row_id":"ROW","amount_usd":100}]', **values))

    def test_cash_is_counted_once_before_and_after_payroll_arrives(self):
        source = self.source()
        period = frappe._dict(name="P", reconciliation_mode="Operativa", applied_usd=0, collection_rows=[])
        before = calculate_totals(period, [source])
        self.assertEqual(before, dict(applied_total_usd=100, remitted_total_usd=100, pending_usd=0,
                                     deposit_coverage_usd=100, complementary_coverage_usd=0))
        period.applied_usd = 100
        period.collection_rows = [frappe._dict(name="ROW", applied_usd=100, remitted_usd=100,
            remittance_detail='[{"importe_usd":100,"aplicacion_directa":"APP"}]')]
        self.assertEqual(calculate_totals(period, [source]), before)

    def test_direct_and_legacy_cash_keep_independent_capacity(self):
        source = self.source()
        source.amount = source.historical_remitted_usd = 60
        source.application_allocation_detail = '[{"collection_row_id":"ROW","amount_usd":60}]'
        source.historical_detail = '[{"importe_usd":60}]'
        period = frappe._dict(reconciliation_mode="Operativa", applied_usd=100, collection_rows=[
            frappe._dict(name="ROW", applied_usd=100, rounding_adjustment_usd=0,
                         remittance_detail='[{"importe_usd":60,"aplicacion_directa":"APP"},{"importe_usd":20}]')])
        self.assertEqual(calculate_totals(period, [source]), dict(applied_total_usd=100, remitted_total_usd=80, pending_usd=20,
                                                               deposit_coverage_usd=80, complementary_coverage_usd=0))

    def test_close_rejects_missing_first_or_second_including_unmatched_extra_application(self):
        period = frappe._dict(name="P", collection_rows=[frappe._dict(name="ROW")])
        for values in [dict(match_status="Excepcion"),
                       dict(historical_balance_usd=1, deposit_match_status="Depósito parcial")]:
            source = self.source(); source.update(values)
            with self.subTest(values=values), patch.object(frappe, "get_all", return_value=[source]), \
                 patch.object(frappe, "throw", side_effect=ValueError):
                with self.assertRaises(ValueError):
                    validate_direct_applications(period)
        for links in ('[]', '[{"collection_row_id":"ROW","amount_usd":99}]'):
            source = self.source()
            source.update(quality_status="Sin cobranza vinculada", application_allocation_detail=links)
            with patch.object(frappe, "get_all", return_value=[source]):
                validate_direct_applications(period)

    def test_close_accepts_informational_difference_with_complete_links_and_payment(self):
        period = frappe._dict(name="P", collection_rows=[frappe._dict(name="ROW")])
        source = self.source()
        source.quality_status = "Con diferencias"
        with patch.object(frappe, "get_all", return_value=[source]):
            validate_direct_applications(period)
        self.assertEqual(source.quality_status, "Con diferencias")

    def test_close_accepts_cash_with_confirmed_application_adjustment(self):
        period = frappe._dict(name="P", collection_rows=[frappe._dict(name="ROW")])
        source = self.source()
        source.update(application_adjustment_usd=20, historical_remitted_usd=80,
            deposit_match_status="Conciliada: depósito + ajuste",
            application_allocation_detail='[{"collection_row_id":"ROW","amount_usd":80}]')
        with patch.object(frappe, "get_all", return_value=[source]):
            validate_direct_applications(period)

    def test_close_rejects_overcoverage_even_when_saved_status_says_settled(self):
        period = frappe._dict(name="P", collection_rows=[])
        source = self.source()
        source.historical_remitted_usd = 100.01
        with patch.object(frappe, 'get_all', return_value=[source]), patch.object(frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
            validate_direct_applications(period)

    def test_period_does_not_net_excess_of_one_application_against_another(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
        period = frappe._dict(name='P', reconciliation_mode='Historica', status='Pendiente', collection_rows=[])
        sources = [self.source() for _ in range(2)]
        for row, name in zip(sources, ('A', 'B')):
            row.update(name=name, event_type='Aplicacion', historical_period='P', application_adjustment_usd=0)
        account = frappe._dict(reference='D', voucher='V', event_date='2025-04-30', unclassified_usd=0)
        allocation = dict(allocations=[dict(claim_id='H:A', deposit_id='D', amount_usd=120, origin='Manual'),
                                      dict(claim_id='H:B', deposit_id='D', amount_usd=80, origin='Manual')],
                          deposit_meta={'D': {'account': account}}, rounding_movements=[])
        with patch('credinomina_reconciliation.credit_application.with_funding', side_effect=lambda value, _rows: value), \
             patch.object(engine, '_save_reconciled_document'):
            engine._rebuild_historical_balances([period], sources, allocation)
        self.assertEqual(sources[0].deposit_match_status, 'Diferencia de importe')
        self.assertEqual(sources[1].deposit_match_status, 'Depósito parcial')
        self.assertEqual(period.status, 'Parcial')


if __name__ == "__main__":
    unittest.main()
