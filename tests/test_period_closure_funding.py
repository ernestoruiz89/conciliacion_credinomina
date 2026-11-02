"""Closing requires arithmetic funding in USD, independently of saved labels."""
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.period_closure import validate_direct_applications
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as controller


class PeriodClosureFundingTests(unittest.TestCase):
    def source(self, **values):
        return frappe._dict(dict(name='A', event_type='Aplicacion', currency='USD', amount=100,
            match_status='Conciliado', historical_remitted_usd=100, historical_balance_usd=0,
            deposit_match_status='Depósito conciliado', historical_detail='[]'), **values)

    def validate(self, source):
        with patch.object(frappe, 'get_all', return_value=[source]) as read, \
             patch.object(frappe, 'throw', side_effect=ValueError):
            validate_direct_applications(frappe._dict(name='P'))
        return read.call_args.kwargs['fields']

    def test_saved_settled_labels_cannot_hide_a_funding_shortfall(self):
        for paid in (0, 99, 99.99):
            with self.subTest(paid=paid), self.assertRaises(ValueError):
                self.validate(self.source(historical_remitted_usd=paid))

    def test_nio_amount_is_converted_before_validating_usd_funding(self):
        source = self.source(currency='NIO', amount=3662.43, manual_fx_rate=36.6243)
        fields = self.validate(source)
        self.assertTrue({'currency', 'manual_fx_rate', 'equivalent_currency', 'equivalent_amount', 'fx_basis'} <= set(fields))
        for paid in (99.99, 100.01):
            with self.subTest(paid=paid), self.assertRaises(ValueError):
                self.validate(frappe._dict(source, historical_remitted_usd=paid))

    def test_documented_equivalent_and_adjustment_are_applied_once(self):
        self.validate(self.source(currency='NIO', amount=3662.43, equivalent_currency='USD',
            equivalent_amount=100, fx_basis='Equivalente del archivo', application_adjustment_usd=20,
            historical_remitted_usd=80, deposit_match_status='Conciliada: depósito + ajuste'))

    def test_missing_conversion_and_excessive_adjustment_cannot_be_closed(self):
        for values in (dict(currency='NIO', amount=3662.43), dict(currency='EUR'),
                       dict(application_adjustment_usd=101, historical_remitted_usd=0)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.validate(self.source(**values))

    def test_signed_confirmed_difference_preserves_exact_settlement(self):
        for difference, cash in ((-.01, 99.99), (.01, 100.01)):
            with self.subTest(difference=difference):
                self.validate(self.source(historical_remitted_usd=cash,
                    historical_detail=[dict(diferencia_usd=difference)]))

    def test_operative_close_rejects_arithmetic_pending_despite_saved_settled_rows(self):
        from unittest.mock import Mock
        period = frappe._dict(name='P', employer='E', reconciliation_mode='Operativa', status='Conciliado',
            check_permission=Mock(), recalculate_totals=Mock(), pending_usd=.01,
            collection_rows=[frappe._dict(application_status='Aplicado y remitido', applied_usd=100, remitted_usd=99.99)])
        with patch.object(frappe, 'get_doc', return_value=period), \
             patch.object(controller, 'lock_period_companies'), \
             patch.object(frappe, 'throw', side_effect=ValueError) as reject, patch.object(frappe.db, 'count', return_value=0), \
             patch.object(controller, '_reconcile_if_sources', return_value=None), \
             patch.object(controller, '_pending_remittance_details_for_period', return_value=[]), \
             patch.object(controller, '_mark_period_closed') as mark_closed:
            with self.assertRaises(ValueError):
                controller.close_period('P')
        self.assertIn('pendientes de cubrir', reject.call_args.args[0])
        mark_closed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
