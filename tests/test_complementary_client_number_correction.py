import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import complementary_loan_correction as correction


class ClientNumberCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.previous = frappe._dict(name='X', docstatus=1, category='Ajuste de conciliación',
            review_action='Partida de depósito', client_number='MARVIN JOSE URBINA', employer='EMP', amount_usd=19.32)
        self.document = frappe._dict(self.previous, client_number='5586')
        self.deposit = frappe._dict(name='D', docstatus=1, check_permission=Mock(),
            detail_rows=[frappe._dict(name='ROW', source_row=2, client_number='5586')],
            targets=[frappe._dict(complementary_item='X', detail_row='ROW')], allocation_detail='[]')

    def check(self, closed=False):
        with patch.object(correction, '_linked_deposits', return_value=[self.deposit]), \
                patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool'), \
                patch('credinomina_reconciliation.paying_employers.reconciliation_companies', return_value=['EMP']), \
                patch('credinomina_reconciliation.complementary_cancellation._deposit_periods', return_value={'P'}), \
                patch.object(frappe, 'get_all', return_value=['P'] if closed else []):
            correction.validate_submitted_client_number(self.document, self.previous)

    def test_name_can_be_corrected_to_number_without_changing_deposit_or_cash(self):
        before = copy.deepcopy({key: value for key, value in self.deposit.items() if key != 'check_permission'})
        self.check()
        self.assertEqual({key: value for key, value in self.deposit.items() if key != 'check_permission'}, before)
        self.assertEqual(self.document.amount_usd, 19.32)

    def test_wrong_or_cleared_number_cannot_break_linked_detail(self):
        for number in ('OTHER', ''):
            self.document.client_number = number
            with self.subTest(number=number), self.assertRaisesRegex(frappe.ValidationError, 'D, fila 2.*5586'):
                self.check()

    def test_client_change_cannot_reassign_an_existing_confirmed_allocation(self):
        for targets in (self.deposit.targets, []):
            self.deposit.targets = targets
            self.deposit.allocation_detail = json.dumps([dict(partida='X', importe_usd=19.32)])
            with self.subTest(manual=bool(targets)), self.assertRaisesRegex(frappe.ValidationError, 'asignación confirmada'):
                self.check()

    def test_equivalent_number_format_does_not_change_beneficiary(self):
        self.previous.client_number = '005586'
        self.deposit.allocation_detail = json.dumps([dict(partida='X', importe_usd=19.32)])
        self.check()

    def test_closed_period_and_special_treatments_stay_protected(self):
        with self.assertRaisesRegex(frappe.ValidationError, 'Reabra los períodos P'):
            self.check(closed=True)
        for change in (dict(category='Saldo a favor del cliente'), dict(category='Ajuste de aplicación'),
                       dict(generic_distribution=1), dict(origin_deposit='D'), dict(receivable_origin='R'),
                       dict(subcategory_effect='CxC al Empleado'), dict(compensations=[{}])):
            with self.subTest(change=change), self.assertRaisesRegex(frappe.ValidationError, 'conserva el número de cliente'):
                correction.validate_submitted_client_number(frappe._dict(self.document | change), self.previous)

    def test_unchanged_or_draft_data_needs_no_financial_queries(self):
        for doc in (self.previous, frappe._dict(self.document, docstatus=0)):
            with patch.object(correction, '_linked_deposits') as query:
                correction.validate_submitted_client_number(doc, self.previous)
                query.assert_not_called()

    def test_schema_allows_submitted_correction(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / 'credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.json').read_text(encoding='utf-8'))
        self.assertTrue(next(field for field in schema['fields'] if field['fieldname'] == 'client_number')['allow_on_submit'])
