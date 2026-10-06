import copy
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import provisional_adjustments as module


class ProvisionalTests(unittest.TestCase):
    def setUp(self):
        self.throw = patch.object(module.frappe, 'throw', side_effect=ValueError)
        self.throw.start()
        self.addCleanup(self.throw.stop)
        self.row = frappe._dict(name='C1', idx=1, row_key='K1', client='1', client_name='Cliente',
            client_number='1', loan_number='109-1', expected_usd=100, expected_nio=3662.43,
            applied_usd=90, deduction_status='Pendiente de detalle', deducted_usd=0,
            deducted_nio=0, remitted_usd=0, remittance_detail='[]')
        self.period = frappe._dict(name='P', employer='E', collection_rows=[self.row], provisional_adjustments=[])
        self.deposit = frappe._dict(name='D', employer='E', amount_usd=100, targets=[], detail_rows=[], modified='now')

    def proposal(self, **kwargs):
        return frappe._dict({**module.row_basis(self.period, self.row), 'name': 'PR1', 'state': 'Aprobado',
            'category': 'Otros ingresos', 'description': 'Diferencia revisada', 'approved_by': 'Operator', **kwargs})

    def plan(self):
        with patch.object(module, '_verified_rows', return_value=[]), \
             patch('credinomina_reconciliation.client_credit.load_credits', return_value=[]):
            return module._plan(self.deposit, [self.period])

    def test_signed_difference_uses_base_not_previous_financial_complements(self):
        self.row.complementary_usd = 10
        self.assertEqual(module.row_basis(self.period, self.row)['amount_usd'], 10)
        self.row.applied_usd = 110
        self.assertEqual(module.row_basis(self.period, self.row)['amount_usd'], -10)
        self.assertEqual(self.row.deducted_usd, 0)

    def test_deduction_in_nio_is_converted_and_invalid_detail_not_silently_replaced(self):
        self.row.update(deduction_status='Deduccion parcial', deducted_nio=3296.187)
        self.assertEqual(module.row_basis(self.period, self.row)['base_usd'], 90)
        self.row.expected_usd = 0
        with self.assertRaises(ValueError):
            module.row_basis(self.period, self.row)

    def test_inference_does_not_change_base_fingerprint(self):
        before = module.row_basis(self.period, self.row)
        self.row.update(deduction_status='Inferida por depósito', deducted_usd=100, deducted_nio=3662.43)
        self.assertEqual(module.row_basis(self.period, self.row)['fingerprint'], before['fingerprint'])

    def test_browser_numeric_serialization_does_not_invalidate_approval(self):
        self.saved_period()
        fingerprint = module.row_basis(self.period, self.row)['fingerprint']
        self.row.expected_usd = 100.0
        self.row.applied_usd = '90.00'
        proposal = self.period.provisional_adjustments[0]
        proposal.base_usd, proposal.applied_usd, proposal.amount_usd = 100, '90.00', 10
        self.assertEqual(module.row_basis(self.period, self.row)['fingerprint'], fingerprint)
        module.guard_period(self.period)
        self.assertEqual(proposal.state, 'Aprobado')

    def test_plan_is_non_mutating_and_contains_the_signed_distribution(self):
        self.period.provisional_adjustments = [self.proposal()]
        before = copy.deepcopy(dict(self.period))
        plan = self.plan()
        self.assertEqual((plan['total_usd'], plan['applied_usd'], plan['adjustment_usd']), (100, 90, 10))
        self.assertEqual(dict(self.period), before)

    def test_multiple_periods_with_same_client_keep_distinct_exact_row_links(self):
        self.period.provisional_adjustments = [self.proposal()]
        other = copy.deepcopy(self.period)
        other.name = 'P2'
        other.collection_rows[0].name, other.collection_rows[0].row_key = 'C2', 'K2'
        other.provisional_adjustments[0].update(module.row_basis(other, other.collection_rows[0]))
        other.provisional_adjustments[0].name = 'PR2'
        self.deposit.amount_usd = 200
        with patch.object(module, '_verified_rows', return_value=[]), \
             patch('credinomina_reconciliation.client_credit.load_credits', return_value=[]):
            plan = module._plan(self.deposit, [self.period, other])
        self.assertEqual(plan['total_usd'], 200)
        self.assertEqual([row['collection_row'] for row in plan['rows']], ['C1', 'C2'])
        self.assertEqual([row['collection_row'] for row in plan['adjustments']], ['C1', 'C2'])

    def test_unapproved_stale_missing_or_zeroed_adjustment_is_rejected(self):
        for value in (None, self.proposal(state='Pendiente de revisión'), self.proposal(fingerprint='stale')):
            self.period.provisional_adjustments = [value] if value else []
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.plan()
        self.period.provisional_adjustments = [self.proposal()]
        self.row.applied_usd = 100
        with self.assertRaises(ValueError):
            self.plan()

    def test_partial_deposit_and_existing_distribution_are_not_overwritten(self):
        self.period.provisional_adjustments = [self.proposal()]
        for field, value in [('amount_usd', 99), ('targets', [frappe._dict(name='T')]),
                             ('detail_rows', [frappe._dict(name='DR')]), ('allocated_usd', 1)]:
            old = self.deposit.get(field)
            self.deposit[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.plan()
            self.deposit[field] = old

    def saved_period(self):
        self.period.provisional_adjustments = [self.proposal()]
        previous = copy.deepcopy(self.period)
        self.period.get_doc_before_save = lambda: previous
        return previous

    def test_classification_edit_and_base_change_revoke_approval(self):
        self.saved_period()
        self.period.provisional_adjustments[0].description = 'Corrección'
        module.guard_period(self.period)
        self.assertEqual(self.period.provisional_adjustments[0].state, 'Pendiente de revisión')
        self.assertIsNone(self.period.provisional_adjustments[0].approved_by)
        self.saved_period()
        self.row.applied_usd = 89
        module.guard_period(self.period)
        self.assertEqual(self.period.provisional_adjustments[0].state, 'Pendiente de revisión')

    def test_api_cannot_forge_approval_amount_trace_or_remove_rows(self):
        for field, value in [('state', 'Materializado'), ('amount_usd', 999), ('deposit', 'D'), ('fingerprint', 'fake')]:
            self.saved_period()
            self.period.provisional_adjustments[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                module.guard_period(self.period)
        self.saved_period()
        self.period.provisional_adjustments = []
        with self.assertRaises(ValueError):
            module.guard_period(self.period)

    def test_materialized_classification_cannot_be_silently_rewritten(self):
        previous = self.saved_period()
        previous.provisional_adjustments[0].state = 'Materializado'
        proposal = self.period.provisional_adjustments[0]
        proposal.state, proposal.category = 'Materializado', 'Cobranza administrativa'
        with self.assertRaises(ValueError):
            module.guard_period(self.period)

    def test_credits_require_tracking_and_adjustments_require_definite_subcategory(self):
        for category in module.CREDITS:
            with self.assertRaises(ValueError):
                module.validate_classification(self.proposal(category=category))
        with patch.object(module.frappe, 'db', Mock(get_value=Mock(return_value='Por clasificar'))):
            with self.assertRaises(ValueError):
                module.validate_classification(self.proposal(category='Ajuste de conciliación', subcategory='x'))

    def test_materialized_exact_link_distinguishes_repeated_credit_rows(self):
        self.period.provisional_adjustments = [self.proposal(state='Materializado', complementary_item='X')]
        self.assertEqual(module.explicit_complement_rows([self.period]), {'X': 'C1'})
        self.period.provisional_adjustments[0].state = 'Aprobado'
        self.assertEqual(module.explicit_complement_rows([self.period]), {})

    def test_explicit_confirmation_required_before_loading_or_mutating(self):
        with patch.object(module, '_deposit_context') as load:
            with self.assertRaises(ValueError):
                module.apply_transfer('D', 'fingerprint', False)
            load.assert_not_called()

    def test_successful_retry_does_not_recreate_items(self):
        self.period.prepared_deposit = 'D'
        self.deposit.result = 'Conciliado'
        with patch.object(module, '_deposit_context', return_value=(self.deposit, [self.period])), \
             patch.object(module, '_plan') as plan, patch.object(module.frappe, 'new_doc') as new:
            result = module.apply_transfer('D', 'old', True)
            self.assertTrue(result['already_applied'])
            new.assert_not_called()
            plan.assert_not_called()


if __name__ == '__main__':
    unittest.main()
