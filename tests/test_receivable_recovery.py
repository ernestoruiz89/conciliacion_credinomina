"""Recoveries settle their origin, never the mere posting of an adjustment."""
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import receivable_recovery as recovery
from credinomina_reconciliation import complementary_compensation as offsets
from credinomina_reconciliation import deposit_adjustment_receivables as receivables
from credinomina_reconciliation.complementary_balances import financial_balance


def origin(**changes):
    return frappe._dict(dict(name='ORIGIN', docstatus=1, category='Ajuste de conciliación',
        subcategory_effect='CxC a la empresa', amount_usd=-10, employer='E',
        posting_date='2025-04-01', accounting_status='Registrada', **changes))


def receipt(name='RECEIPT', **changes):
    values = dict(name=name, docstatus=1, category=recovery.CATEGORY, employer='E',
        amount_usd=3, amount=3, posting_date='2025-05-01', receivable_origin='ORIGIN')
    values.update(changes)
    return frappe._dict(values)


class ReceivableRecoveryTests(unittest.TestCase):
    def balances(self, item):
        return {item.name: financial_balance(item, [dict(deposit='D0', amount_usd=-10, employer='E')])}

    def test_partial_cash_offset_and_reversal_preserve_original_debt(self):
        item, cash = origin(), receipt()
        offset = receipt('OFFSET', category=offsets.CATEGORY, amount_usd=4, compensated_usd=4)
        balances = self.balances(item)
        self.assertEqual(balances[item.name]['pending_usd'], 0)
        settled = {cash.name: {'distributions': [dict(deposit='D1', amount_usd=3)]}}
        def row():
            return receivables.build_receivables([item], balances, settlements=[cash, offset], settlement_balances=settled)[0]
        self.assertEqual(row()['receivable_usd'], 3)
        offset.compensated_usd = 0
        self.assertEqual(row()['receivable_usd'], 7)
        self.assertEqual(item.amount_usd, -10)
        cash.docstatus = 2
        self.assertEqual(row()['receivable_usd'], 10)

    def test_selected_destination_and_core_registration_do_not_repay(self):
        item, cash = origin(), receipt()
        result = receivables.build_receivables([item], self.balances(item), settlements=[cash],
            settlement_balances={cash.name: {'distributions': []}})
        self.assertEqual(result[0]['receivable_usd'], 10)
        self.assertEqual(result[0]['receivable_paid_usd'], 0)

    def test_planned_receipt_reserves_only_remaining_capacity(self):
        cash = receipt(amount_usd=6)
        rows = [dict(employer='E', receivable_usd=8)]
        with patch.object(frappe, 'get_doc', return_value=origin(check_permission=Mock())), \
             patch.object(recovery, 'position', return_value=(rows, [cash], {cash.name: {'used_usd': 2}})):
            result = recovery.preview_recovery('ORIGIN')
        self.assertEqual(result['companies'][0]['available_usd'], 4)

    def test_lock_pool_then_origin_then_pair_also_for_recompensation(self):
        events = []
        docs = {'ORIGIN': origin(check_permission=Mock()),
            'RECEIPT': receipt(category=offsets.CATEGORY, compensated_usd=0, check_permission=Mock()),
            'CREDIT': frappe._dict(name='CREDIT', employer='E', check_permission=Mock())}
        def read(doctype, name, **kwargs):
            events.append(('locked' if kwargs.get('for_update') else 'read', name))
            return docs[name]
        with patch.object(frappe, 'get_doc', side_effect=read), \
             patch('credinomina_reconciliation.paying_employers.reconciliation_companies', return_value=['E']), \
             patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool', side_effect=lambda scope: events.append(('pool', scope))):
            offsets._load_pair('RECEIPT', 'CREDIT', 'submit')
        self.assertEqual(events, [('read', 'CREDIT'), ('read', 'RECEIPT'), ('pool', {'E'}),
            ('locked', 'ORIGIN'), ('locked', 'CREDIT'), ('locked', 'RECEIPT')])

    def test_ordinary_pair_does_not_require_cash_pool(self):
        docs = [frappe._dict(name=name, check_permission=Mock()) for name in ('A', 'B')]
        with patch.object(frappe, 'get_doc', side_effect=lambda doctype, name, **kw: docs[name == 'B']), \
             patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool') as lock:
            offsets._load_pair('A', 'B')
        lock.assert_not_called()

    def test_permission_failure_does_not_lock_or_write(self):
        doc = receipt(check_permission=Mock(side_effect=frappe.PermissionError))
        with patch.object(frappe, 'get_doc', return_value=doc), \
             patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool') as lock, \
             self.assertRaises(frappe.PermissionError):
            offsets._load_pair('RECEIPT', 'CREDIT')
        lock.assert_not_called()

    def test_another_recovery_is_not_a_credit_counterpart(self):
        with patch.object(frappe, 'throw', side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
            recovery.validate_compensation_recovery([receipt(), receipt('OTHER')], 1)

    def test_cancelled_receipts_can_be_loaded_for_history_but_not_settlement(self):
        with patch.object(receivables, 'records', return_value=[]) as read:
            receivables.load_settlements([origin()], include_cancelled=True)
            self.assertNotIn('docstatus', read.call_args.kwargs['filters'])
            receivables.load_settlements([origin()])
            self.assertEqual(read.call_args.kwargs['filters']['docstatus'], ['!=', 2])

    def test_origin_cannot_be_undistributed_while_recovery_is_used(self):
        item, cash = origin(), receipt()
        records = [[item], [cash]]
        balances = {**self.balances(item), cash.name: financial_balance(cash, [dict(deposit='D1', amount_usd=3)])}
        with patch.object(frappe, 'get_all', side_effect=records), \
             patch('credinomina_reconciliation.complementary_balances.load_balances', return_value=balances), \
             patch.object(frappe, 'throw', side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
            recovery.validate_deposit_distributions([item, cash], {'D0': []})

    def test_cancelled_request_key_does_not_report_success(self):
        item = origin(check_permission=Mock())
        cash = receipt(docstatus=2, check_permission=Mock(), receivable_method='Depósito',
            receivable_counterpart='D1', description='Cobro', posting_date='2025-05-01')
        def reject(message):
            raise frappe.ValidationError(message)
        with patch.object(frappe, 'get_doc', side_effect=[item, item, cash]), \
             patch.object(frappe, 'db', Mock(get_value=Mock(return_value=cash.name))), \
             patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool'), \
             patch('credinomina_reconciliation.paying_employers.reconciliation_companies', return_value=['E']), \
             patch.object(frappe, 'throw', side_effect=reject), \
             self.assertRaisesRegex(frappe.ValidationError, 'cancelado'):
            recovery.apply_recovery(item.name, 'E', 'Depósito', 'D1', 3, '2025-05-01', 'Cobro', 'a' * 32)

    def test_canceled_target_explains_correction_without_deleting_history(self):
        deposit = frappe._dict(name='D1', employer='E', docstatus=1, amount_usd=10,
            allocated_usd=0, justified_surplus_usd=0, check_permission=Mock(),
            targets=[frappe._dict(complementary_item='CANCELED'), frappe._dict(complementary_item='ACTIVE')])
        with patch('credinomina_reconciliation.paying_employers.allowed_employers', return_value={'E'}), \
             patch.object(frappe, 'get_all', return_value=['ACTIVE']) as read, \
             patch.object(frappe, 'throw', side_effect=lambda msg: (_ for _ in ()).throw(frappe.ValidationError(msg))):
            with self.assertRaisesRegex(frappe.ValidationError, 'retire o corrija.*historial'):
                recovery._check_cash_destination(deposit, 'E', 3)
        self.assertEqual(read.call_args.kwargs['filters']['name'], ['in', ['ACTIVE', 'CANCELED']])
        self.assertEqual(len(deposit.targets), 2)

    def test_cash_destination_checks_permissions_before_reading_targets(self):
        deposit = frappe._dict(check_permission=Mock(side_effect=frappe.PermissionError))
        with patch.object(frappe, 'get_all') as read, self.assertRaises(frappe.PermissionError):
            recovery._check_cash_destination(deposit, 'E', 3)
        read.assert_not_called()
