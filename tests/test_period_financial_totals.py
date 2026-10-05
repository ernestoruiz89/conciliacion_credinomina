import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.period_totals import PeriodTotalsContext, calculate_totals


class PeriodFinancialTotalsTests(unittest.TestCase):
    def historical(self, net=80, cash=80, adjustment=20, **source_values):
        period = {"reconciliation_mode": "Historica", "applied_usd": net}
        source = {"currency": "USD", "event_type": "Aplicacion", "amount": net + adjustment,
                  "application_adjustment_usd": adjustment, "historical_remitted_usd": cash,
                  **source_values}
        return calculate_totals(period, [source], adjustment)

    def assert_totals(self, values, applied, covered, pending):
        self.assertEqual(values, dict(applied_total_usd=applied, remitted_total_usd=covered, pending_usd=pending))

    def test_original_application_with_deposit_and_confirmed_offset(self):
        self.assert_totals(self.historical(), 100, 100, 0)
        self.assert_totals(self.historical(cash=30), 100, 50, 50)
        self.assert_totals(self.historical(net=0, cash=0, adjustment=100), 100, 100, 0)

    def test_converted_cordobas_are_not_added_to_usd_again(self):
        period = dict(reconciliation_mode="Historica", applied_usd=22.52, applied_nio=824.78)
        row = dict(event_type="Aplicacion", currency="NIO", amount=824.78,
                   manual_fx_rate=36.6243, historical_remitted_usd=22.52)
        self.assert_totals(calculate_totals(period, [row]), 22.52, 22.52, 0)

    def test_operational_fee_cash_is_not_application_coverage(self):
        period = dict(reconciliation_mode="Operativa", applied_usd=80, applied_nio=2929.944,
            complementary_usd=10, remitted_usd=90, collection_rows=[dict(applied_usd=80,
                remittance_detail=[dict(destino="Cobranza", importe_usd=80),
                                   dict(destino="Partida complementaria", importe_usd=10)])])
        self.assert_totals(calculate_totals(period, application_adjustment_usd=20), 100, 100, 0)

    def test_collection_without_applications_is_not_a_receivable(self):
        period = dict(reconciliation_mode="Operativa", collection_rows=[dict(expected_usd=100, deducted_usd=80)])
        self.assert_totals(calculate_totals(period), 0, 0, 0)

    def test_credit_on_one_customer_does_not_hide_another_customers_debt(self):
        period = dict(reconciliation_mode="Operativa", applied_usd=100, collection_rows=[
            dict(applied_usd=50, remittance_detail=[dict(destino="Cobranza", importe_usd=60)]),
            dict(applied_usd=50, remittance_detail=[dict(destino="Cobranza", importe_usd=40)])])
        self.assert_totals(calculate_totals(period), 100, 100, 10)

    def test_signed_tolerance_is_coverage_once_not_cash_twice(self):
        for delta, cash in [(-0.01, 46.51), (0.01, 46.53)]:
            with self.subTest(delta=delta):
                self.assert_totals(self.historical(net=46.52, cash=cash, adjustment=0,
                    historical_detail=[dict(diferencia_usd=delta)]), 46.52, 46.52, 0)

    def test_rebuild_uses_current_rows_and_offset_is_counted_in_one_period(self):
        p1 = frappe._dict(name="P1", employer="E", reconciliation_mode="Historica", applied_usd=80)
        p2 = frappe._dict(name="P2", employer="E", reconciliation_mode="Historica", applied_usd=0)
        row = frappe._dict(name="APP", historical_period="P1", event_type="Aplicacion", effective=1,
            match_status="Conciliado", amount=100, currency="USD", application_adjustment_usd=20,
            historical_remitted_usd=80)
        item = frappe._dict(period="P1", related_application="APP", application_adjustment_usd=20,
                            adjustment_periods='["P1", "P2"]')
        def query(doctype, **kwargs):
            self.assertEqual(doctype, "CN Complementary Item", "Current rows must not be replaced by stale database rows")
            self.assertEqual(kwargs["filters"]["docstatus"], 1)
            self.assertEqual(kwargs["filters"]["category"], "Ajuste de aplicación")
            return [item]
        with patch.object(frappe, "get_all", side_effect=query) as read:
            context = PeriodTotalsContext([p1, p2], [row])
            self.assert_totals(context.values(p1), 100, 100, 0)
            self.assert_totals(context.values(p2), 0, 0, 0)
            read.assert_called_once()


if __name__ == "__main__":
    unittest.main()
