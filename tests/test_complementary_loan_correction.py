import json
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import complementary_loan_correction as correction


class ComplementaryLoanCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.previous = frappe._dict(name='X', docstatus=1, category='Ajuste de conciliación',
            review_action='Partida de depósito', loan_number='', employer='MIDESA', amount_usd=26.32)
        self.document = frappe._dict(self.previous, loan_number='0011685-1')
        self.deposit = frappe._dict(name='D', docstatus=1, check_permission=Mock(),
            detail_rows=[frappe._dict(name='ROW', loan_number='0011685-1', source_row=2)],
            targets=[frappe._dict(complementary_item='X', detail_row='ROW')],
            allocation_detail='[{"partida":"X","importe_usd":26.32}]')

    def run_check(self, *, closed=False, automatic=False):
        def records(doctype, **kwargs):
            if doctype == 'CN Remittance Target':
                return [] if automatic else ['D']
            if doctype == 'CN Remittance Allocation':
                return [self.deposit]
            if doctype == 'CN Reconciliation Period':
                return ['CLOSED'] if closed else []
            return []
        with patch.object(frappe, 'get_all', side_effect=records), \
             patch.object(frappe, 'get_doc', return_value=self.deposit), \
             patch('credinomina_reconciliation.complementary_cancellation._deposit_periods', return_value={'P'}):
            correction.validate_submitted_loan(self.document, self.previous)

    def test_fill_credit_matches_linked_detail_without_changing_money_or_targets(self):
        before = dict(self.deposit)
        self.run_check()
        self.assertEqual(self.deposit, before)
        self.assertEqual(self.document.amount_usd, 26.32)
        self.deposit.check_permission.assert_called_once_with('read')

    def test_different_or_cleared_credit_cannot_break_existing_detail(self):
        for value in ('OTHER', ''):
            with self.subTest(value=value):
                self.previous.loan_number = '0011685-1'
                self.document.loan_number = value
                with self.assertRaisesRegex(frappe.ValidationError, 'D, fila 2.*0011685-1'):
                    self.run_check()

    def test_closed_period_blocks_even_when_credit_matches_detail(self):
        with self.assertRaisesRegex(frappe.ValidationError, 'CLOSED'):
            self.run_check(closed=True)

    def test_special_financial_treatments_keep_existing_protection(self):
        for changes in ({'category': 'Saldo a favor del cliente'}, {'category': 'Ajuste de aplicación'},
                        {'generic_distribution': 1}, {'origin_deposit': 'D'}, {'receivable_origin': 'ORIGIN'},
                        {'registration_exception': 'EXCEPTION'}, {'subcategory_effect': 'CxC al Empleado'},
                        {'compensations': [{'amount_usd': 1}]}):
            with self.subTest(changes=changes):
                doc = frappe._dict(self.document | changes)
                with self.assertRaisesRegex(frappe.ValidationError, 'conserva el crédito'), \
                     patch.object(frappe, 'get_all') as query:
                    correction.validate_submitted_loan(doc, self.previous)
                query.assert_not_called()

    def test_draft_or_unchanged_credit_does_not_load_financial_dependencies(self):
        for doc in (frappe._dict(self.previous), frappe._dict(self.document, docstatus=0)):
            with patch.object(frappe, 'get_all') as query:
                correction.validate_submitted_loan(doc, self.previous)
            query.assert_not_called()

    def test_automatic_allocation_cannot_be_moved_to_a_different_credit(self):
        self.previous.loan_number = 'OLD'
        self.deposit.targets = []
        with self.assertRaisesRegex(frappe.ValidationError, 'Desconcilie el depósito D'):
            self.run_check(automatic=True)

    def test_json_substring_does_not_link_an_unrelated_deposit(self):
        self.deposit.allocation_detail = json.dumps([{'partida': 'X10'}])
        with patch.object(frappe, 'get_all', side_effect=[[], [self.deposit]]), \
             patch.object(frappe, 'get_doc') as get_doc, \
             patch('credinomina_reconciliation.complementary_cancellation._deposit_periods', return_value=set()):
            correction.validate_submitted_loan(self.document, self.previous)
        get_doc.assert_not_called()
