import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import pending_payments as payments
from credinomina_reconciliation.follow_up_queue import filter_work, group_work_cases
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as controller


class PendingPaymentTests(unittest.TestCase):
    def setUp(self):
        self.period = dict(name='P', employer='E', reconciliation_mode='Operativa', application_basis='Cobranza')
        self.deposit = dict(name='DEP', employer='E', amount_usd=113.81, allocated_usd=85.38,
                            unclassified_usd=28.43, allocation_detail='[{"periodo":"P"}]')
        self.collection = dict(name='C', parent='P', client_number='7740', loan_number='13997-1',
                               client_name='Cliente de prueba', expected_usd=28.43, applied_usd=0)
        self.detail = dict(name='D', parent='DEP', client_number='7740', loan_number='13997-1', pending_usd=28.43)

    def build(self, **overrides):
        return payments.build_pending_payments(**dict(dict(periods=[self.period], deposits=[self.deposit],
            collections=[self.collection], details=[self.detail]), **overrides))

    def test_missing_application_is_a_payment_not_credit(self):
        tasks = self.build()
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task['kind'], 'pending_application')
        self.assertEqual(task['amount_usd'], 28.43)
        self.assertEqual(task['collection_row'], 'C')
        self.assertEqual(task['target_name'], 'DEP')
        self.assertEqual(task['period'], 'P')
        self.assertIn('no es un saldo a favor', task['next_action'])
        self.assertEqual(filter_work(group_work_cases(tasks), kind='deposits')[0]['amount_usd'], 28.43)
        self.assertEqual(len(filter_work(group_work_cases(tasks), kind='payments')), 1)
        self.assertEqual(filter_work(group_work_cases(tasks), kind='credits'), [])

    def test_partial_application_and_later_correction(self):
        self.collection['applied_usd'] = 10
        self.detail['pending_usd'] = self.deposit['unclassified_usd'] = 18.43
        self.assertEqual(self.build()[0]['amount_usd'], 18.43)
        self.collection['applied_usd'] = 28.43
        self.assertEqual(self.build(), [])

    def test_no_cobranza_or_ambiguous_identity_does_not_invent_payment(self):
        self.assertEqual(self.build(collections=[]), [])
        self.assertEqual(self.build(collections=[self.collection, dict(self.collection, name='C2')]), [])
        for field, value in [('client_number', 'OTHER'), ('loan_number', ''), ('employer', 'OTHER'),
                             ('application_reference', 'UNMATCHED')]:
            with self.subTest(field=field):
                self.assertEqual(self.build(details=[dict(self.detail, **{field: value})]), [])

    def test_scope_is_explicit_never_inferred_from_deposit_date(self):
        self.deposit['deposit_date'] = '2027-01-01'
        self.assertEqual(len(self.build()), 1)
        self.deposit['allocation_detail'] = '[{"periodo":"OTHER"}]'
        self.assertEqual(self.build(), [])
        self.deposit['allocation_detail'] = '[]'
        self.assertEqual(len(self.build()), 1)  # Unique identified collection across visible periods.
        other_period = dict(self.period, name='OTHER')
        self.assertEqual(self.build(periods=[self.period, other_period],
            collections=[self.collection, dict(self.collection, name='C2', parent='OTHER')]), [])

    def test_excess_documented_credit_and_invalid_destinations_are_not_payments(self):
        self.collection['applied_usd'] = 28.43
        self.assertEqual(self.build(), [])
        self.collection['applied_usd'] = 0
        self.deposit['unclassified_usd'] = 0
        self.assertEqual(self.build(), [])
        self.deposit['unclassified_usd'] = 28.43
        self.deposit['result'] = 'Revisar destinos'
        self.assertEqual(self.build(), [])

    def test_invalid_or_stale_detail_keeps_general_review(self):
        for status in ('Detalle supera depósito', 'Importar detalle actualizado'):
            with self.subTest(status=status):
                self.assertEqual(self.build(deposits=[dict(self.deposit, detail_status=status)]), [])
        self.assertEqual(self.build(details=[self.detail, dict(self.detail, name='D2', pending_usd=-1)]), [])

    def test_selected_basis_and_unknown_conversion_are_respected(self):
        for basis in ('', 'Detalle de empresa'):
            with self.subTest(basis=basis):
                self.assertEqual(self.build(periods=[dict(self.period, application_basis=basis)]), [])
        self.collection.update(expected_usd=0, expected_nio=1000)
        self.assertEqual(self.build(), [])

    def test_multiple_deposits_do_not_duplicate_the_missing_application(self):
        deposits = [dict(self.deposit, unclassified_usd=20), dict(self.deposit, name='DEP2')]
        details = [dict(self.detail, pending_usd=20), dict(self.detail, name='D2', parent='DEP2')]
        self.assertEqual([task['amount_usd'] for task in self.build(deposits=deposits, details=details)], [20, 8.43])

    def test_loader_reads_children_only_from_visible_parents_and_does_not_write(self):
        with patch.object(frappe, 'get_all', side_effect=[[self.collection], [self.detail]]) as query:
            self.assertEqual(len(payments.load_pending_payments([self.period], [self.deposit])), 1)
        self.assertEqual(query.call_args_list[0].kwargs['filters'],
            {'parent': ['in', ['P']], 'parenttype': 'CN Reconciliation Period'})
        self.assertEqual(query.call_args_list[1].kwargs['filters'],
            {'parent': ['in', ['DEP']], 'parenttype': 'CN Remittance Allocation'})
        with patch.object(frappe, 'get_all') as query:
            self.assertEqual(payments.load_pending_payments([], [self.deposit]), [])
            self.assertEqual(payments.load_pending_payments([self.period], []), [])
            query.assert_not_called()


class ReconcileCollectionFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.period = frappe._dict(name='P', employer='E', reconciliation_mode='Operativa',
            application_basis='Cobranza', status='Pendiente',
            collection_rows=[frappe._dict(expected_usd=28.43, applied_usd=0)],
            check_permission=Mock(), reload=Mock())

    def invoke(self, result):
        def throw(message, *args, **kwargs):
            raise ValueError(message)
        with patch.object(frappe, 'get_doc', return_value=self.period), \
             patch.object(frappe, 'has_permission', return_value=True), \
             patch.object(frappe, 'throw', side_effect=throw), \
             patch.object(controller, '_reconcile_if_sources', return_value=result) as reconcile:
            response = controller.reconcile_first('P')
            reconcile.assert_called_once_with('E', preserve_deposits=True)
            return response

    def test_no_sources_reports_an_actionable_error(self):
        with self.assertRaisesRegex(ValueError, 'No hay movimientos importados'):
            self.invoke(None)

    def test_missing_basis_is_explicit(self):
        self.period.application_basis = ''
        with self.assertRaisesRegex(ValueError, 'Seleccione la base'):
            self.invoke({})

    def test_returns_actual_period_quality_after_reload(self):
        self.period.reload.side_effect = lambda: self.period.collection_rows.extend([
            frappe._dict(expected_usd=20, applied_usd=20),
            frappe._dict(expected_usd=0, expected_nio=100),
        ])
        result = self.invoke({'imports': 1, 'rows': 3})
        self.assertEqual({key: result[key] for key in ('period', 'reviewed', 'conforming', 'differences', 'missing_basis')},
                         dict(period='P', reviewed=3, conforming=1, differences=1, missing_basis=1))


if __name__ == '__main__':
    unittest.main()
