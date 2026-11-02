"""Changing FX must not shrink cash below its executed net use."""
import unittest
import copy
from types import MethodType
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as controller


class DepositExecutedCashFloorTests(unittest.TestCase):
    def deposit(self, **changes):
        return frappe._dict(dict(name='D', doctype='CN Remittance Allocation', docstatus=1,
            employer='E', deposit_date='2025-04-30', deposit_reference='D', deposit_voucher='',
            deposit_currency='NIO', deposit_amount=3662.43, fx_rate=36.6243, amount_usd=100,
            allocated_usd=100, justified_surplus_usd=0, detail_rows=[], detail_periods=[], targets=[],
            bank_account=None, _assert_open_related_periods=Mock()), **changes)

    def validate(self, document, *, reversing=False):
        with patch.object(controller, 'allowed_employers', return_value={'E'}), \
             patch.object(frappe, 'get_all', return_value=[]), \
             patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
             patch('credinomina_reconciliation.remittance_portfolio.update_detail_portfolio'):
            controller.CNRemittanceAllocation._validate_deposit(document, reversing=reversing)

    def test_changed_rate_cannot_reduce_cash_below_executed_distribution_after_targets_removed(self):
        document = self.deposit(fx_rate=73.2486)
        with self.assertRaises(frappe.ValidationError):
            self.validate(document)

    def test_saved_credit_reservation_is_part_of_the_minimum_cash(self):
        document = self.deposit(fx_rate=73.2486, allocated_usd=40, justified_surplus_usd=20)
        with self.assertRaises(frappe.ValidationError):
            self.validate(document)

    def test_usd_correction_cannot_reduce_executed_cash_either(self):
        document = self.deposit(deposit_currency='USD', deposit_amount=99.99)
        with self.assertRaises(frappe.ValidationError):
            self.validate(document)

    def test_explicit_reversal_can_withdraw_legacy_overused_cash(self):
        document = self.deposit(fx_rate=73.2486)
        self.validate(document, reversing=True)
        self.assertEqual(document.amount_usd, 50)
        self.assertEqual(document.allocated_usd, 100, 'Validation does not rewrite execution')
        document._assert_open_related_periods.assert_called_once()

    def test_explicit_reversal_can_remove_an_overbudget_plan_but_regular_saves_cannot(self):
        target = frappe._dict(amount_usd=100)
        document = self.deposit(fx_rate=73.2486, allocated_usd=0, targets=[target], _validate_target=Mock())
        with self.assertRaises(frappe.ValidationError):
            self.validate(document)
        self.validate(document, reversing=True)
        self.assertEqual(document.amount_usd, 50)

    def test_explicit_reversal_retains_closed_period_validation(self):
        document = self.deposit(fx_rate=73.2486, _assert_open_related_periods=Mock(side_effect=frappe.ValidationError))
        with self.assertRaises(frappe.ValidationError):
            self.validate(document, reversing=True)

    def test_equal_cash_and_unpaid_rate_corrections_remain_valid(self):
        for document, expected in ((self.deposit(), 100),
                (self.deposit(fx_rate=73.2486, allocated_usd=0), 50)):
            with self.subTest(expected=expected):
                self.validate(document)
                self.assertEqual(document.amount_usd, expected)

    def test_minimum_is_executed_net_cash_not_gross_application_coverage(self):
        # H:100 plus X:-10 uses 90 of real cash, not 100.
        document = self.deposit(deposit_amount=3296.187, allocated_usd=90)
        self.validate(document)
        self.assertEqual(document.amount_usd, 90)

    def test_post_submit_restore_ignores_zero_allocated_values_sent_by_form(self):
        previous = self.deposit(allocation_detail='[{"tipo":"Aplicacion historica","importe_usd":100}]')
        document = self.deposit(fx_rate=73.2486, allocated_usd=0, allocation_detail='[]', flags=frappe._dict(),
            get_doc_before_save=lambda: previous)
        document._validate_deposit = MethodType(controller.CNRemittanceAllocation._validate_deposit, document)
        with patch('credinomina_reconciliation.deposit_difference.guard_rows'), \
             patch('credinomina_reconciliation.difference_reasons.only_detail_reasons_changed', return_value=False), \
             patch('credinomina_reconciliation.accounting_deposits.validate_evidence'), \
             patch('credinomina_reconciliation.client_credit.guard_deposit_changes'), \
             patch.object(controller, 'allowed_employers', return_value={'E'}), \
             patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
             self.assertRaises(frappe.ValidationError):
            controller.CNRemittanceAllocation.before_update_after_submit(document)
        self.assertEqual(document.allocated_usd, 100)
        self.assertEqual(document.allocation_detail, previous.allocation_detail)


class DepositInputLockTests(unittest.TestCase):
    def document(self, **values):
        document = object.__new__(controller.CNRemittanceAllocation)
        document.__dict__.update(dict(doctype='CN Remittance Allocation', name='D', docstatus=1,
            employer='E', flags=frappe._dict(), check_permission=Mock(),
            _reconciliation_inputs_changed=Mock(return_value=True)), **values)
        return document

    def test_changed_inputs_lock_old_and_new_company_before_framework_document_lock(self):
        document = self.document(employer='NEW')
        order = []
        previous = frappe._dict(docstatus=1, employer='OLD')
        with patch.object(frappe, 'get_doc', return_value=previous), \
             patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool', side_effect=lambda companies: order.append(('pool', companies))), \
             patch('credinomina_reconciliation.paying_employers.reconciliation_companies', side_effect=lambda owner: [owner, 'SHARED']), \
             patch.object(controller.Document, '_save', side_effect=lambda *args, **kwargs: order.append(('document', args, kwargs))):
            document._save(ignore_version=True)
        self.assertEqual(order, [('pool', {'OLD', 'NEW', 'SHARED'}), ('document', (), {'ignore_version': True})])

    def test_notes_and_drafts_do_not_lock_cash(self):
        for current, previous, changed in ((1, 1, False), (0, 0, True)):
            with self.subTest(status=current):
                document = self.document(docstatus=current, _reconciliation_inputs_changed=Mock(return_value=changed))
                with patch.object(frappe, 'get_doc', return_value=frappe._dict(docstatus=previous)), \
                     patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool') as lock, \
                     patch.object(controller.Document, '_save'):
                    document._save()
                lock.assert_not_called()

    def test_submit_and_cancel_take_the_pool_lock(self):
        for current, previous in ((1, 0), (2, 1)):
            with self.subTest(current=current):
                document = self.document(docstatus=current)
                with patch.object(frappe, 'get_doc', return_value=frappe._dict(docstatus=previous, employer='E')), \
                     patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool') as lock, \
                     patch('credinomina_reconciliation.paying_employers.reconciliation_companies', return_value=['E']), \
                     patch.object(controller.Document, '_save'):
                    document._save()
                lock.assert_called_once_with({'E'})

    def test_new_documents_reach_framework_without_database_lookup(self):
        document = self.document(__islocal=1)
        with patch.object(frappe, 'get_doc') as read, patch.object(controller.Document, '_save'):
            document._save()
        read.assert_not_called()

    def test_fifo_and_every_matching_detail_input_invalidate_the_cash_snapshot(self):
        previous = frappe._dict(targets=[], detail_periods=[], apply_fifo=0,
            detail_rows=[frappe._dict(name='R', deducted_usd=100)])
        document = copy.deepcopy(previous)
        document.apply_fifo = 1
        self.assertTrue(controller.CNRemittanceAllocation._reconciliation_inputs_changed(document, previous))
        for field, value in (('deducted_usd', 99), ('deducted_nio', 100), ('manual_reconciliation_required', 1),
                ('national_id', 'OTHER'), ('employee_number', 'OTHER'), ('client_name', 'OTHER'),
                ('installment_number', '2'), ('application_reference', 'OTHER'), ('row_key', 'OTHER')):
            with self.subTest(field=field):
                document = copy.deepcopy(previous)
                document.detail_rows[0][field] = value
                self.assertTrue(controller.CNRemittanceAllocation._reconciliation_inputs_changed(document, previous))

    def test_money_format_and_derived_detail_results_do_not_change_cash_inputs(self):
        previous = frappe._dict(targets=[], detail_periods=[], apply_fifo=1,
            detail_rows=[frappe._dict(name='R', deducted_usd=100, manual_reconciliation_required=0)])
        document = copy.deepcopy(previous)
        document.apply_fifo = '1'
        document.detail_rows[0].update(deducted_usd='100.00', manual_reconciliation_required='0',
            linked_usd=100, pending_usd=0, amount_usd=100, match_status='Conciliada')
        self.assertFalse(controller.CNRemittanceAllocation._reconciliation_inputs_changed(document, previous))


if __name__ == '__main__':
    unittest.main()
