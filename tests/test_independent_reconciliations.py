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
        self.assertEqual(before, dict(applied_total_usd=100, remitted_total_usd=100, pending_usd=0))
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
        self.assertEqual(calculate_totals(period, [source]), dict(applied_total_usd=100, remitted_total_usd=80, pending_usd=20))

    def test_close_rejects_missing_first_or_second_including_unmatched_extra_application(self):
        period = frappe._dict(name="P", collection_rows=[frappe._dict(name="ROW")])
        for values in [dict(quality_status="Sin cobranza vinculada", application_allocation_detail="[]"),
                       dict(historical_balance_usd=1, deposit_match_status="Depósito parcial"),
                       dict(application_allocation_detail='[{"collection_row_id":"OTHER","amount_usd":100}]')]:
            source = self.source(); source.update(values)
            with self.subTest(values=values), patch.object(frappe, "get_all", return_value=[source]), \
                 patch.object(frappe, "throw", side_effect=ValueError):
                with self.assertRaises(ValueError):
                    validate_direct_applications(period)
        with patch.object(frappe, "get_all", return_value=[self.source()]):
            validate_direct_applications(period)

    def test_close_accepts_cash_with_confirmed_application_adjustment(self):
        period = frappe._dict(name="P", collection_rows=[frappe._dict(name="ROW")])
        source = self.source()
        source.update(application_adjustment_usd=20, historical_remitted_usd=80,
            deposit_match_status="Conciliada: depósito + ajuste",
            application_allocation_detail='[{"collection_row_id":"ROW","amount_usd":80}]')
        with patch.object(frappe, "get_all", return_value=[source]):
            validate_direct_applications(period)


if __name__ == "__main__":
    unittest.main()
