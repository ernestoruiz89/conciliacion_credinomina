"""Cash-backed credits cannot fund or explain transactions before the bank date."""
import unittest
from datetime import date
from inspect import unwrap
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import client_credit, credit_application, complementary_compensation


class CreditOriginDateTests(unittest.TestCase):
    def item(self, **changes):
        return frappe._dict(dict(name='CREDIT', doctype='CN Complementary Item', docstatus=1,
            category=client_credit.CATEGORY, result=client_credit.RESULT, amount_usd=10,
            registered_deposit='BANK', posting_date='2025-04-01', credit_history='[]',
            modified='stamp', check_permission=Mock()), **changes)

    def test_effective_credit_origin_never_precedes_bank_receipt(self):
        with patch.object(frappe.db, 'get_value', return_value='2025-05-15'):
            self.assertEqual(client_credit.available_from(self.item()), date(2025, 5, 15))
            self.assertEqual(client_credit.available_from(self.item(credit_origin_date='2025-06-01')), date(2025, 6, 1))
            self.assertEqual(complementary_compensation._origin_date(self.item()), date(2025, 5, 15))

    def test_new_origins_before_bank_are_rejected_without_rewriting_evidence(self):
        item = self.item()
        bank = frappe._dict(deposit_date='2025-05-15')
        with patch.object(frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
            client_credit.validate_origin_date(item, bank)
        self.assertEqual(item.posting_date, '2025-04-01')

    def test_reclassified_origin_preserves_core_posting_date_and_actual_bank_origin(self):
        bank = frappe._dict(deposit_date='2025-05-15')
        for posting in ('2025-04-01', '2025-12-01'):
            with self.subTest(posting=posting):
                item = self.item(posting_date=posting, credit_origin_date='2025-05-15')
                client_credit.validate_origin_date(item, bank)
                self.assertEqual(client_credit.available_from(item, bank), date(2025, 5, 15))
                self.assertEqual(item.posting_date, posting)

    def test_legacy_application_before_bank_is_rejected_before_journal_writes(self):
        item = self.item()
        source = frappe._dict(name='A', event_date='2025-04-30')
        with patch.object(credit_application, '_locked_context', return_value=(item, source, frappe._dict(name='P'))), \
             patch.object(frappe.db, 'get_value', return_value='2025-05-15'), \
             patch.object(frappe, 'throw', side_effect=ValueError), \
             patch.object(credit_application, '_save_journal') as save, self.assertRaises(ValueError):
            unwrap(credit_application.apply)('CREDIT', 'stamp', 'A', 10, '2025-04-30')
        save.assert_not_called()
        self.assertEqual(item.credit_history, '[]')

    def test_legacy_external_management_before_bank_is_rejected_before_writes(self):
        item = self.item()
        with patch.object(frappe, 'get_doc', return_value=item), \
             patch.object(frappe.db, 'get_value', return_value='2025-05-15'), \
             patch.object(frappe.db, 'set_value') as save, \
             patch.object(frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
            unwrap(client_credit.record_management)('CREDIT', 'stamp', 'Devolución', 10, '2025-05-14', 'VOUCHER')
        save.assert_not_called()

    def test_legacy_application_reversal_cannot_precede_the_cash_origin(self):
        item = self.item(credit_history='[{"operation_id":"OP","application_id":"A","importe_usd":10,"fecha":"2025-04-30"}]')
        with patch.object(frappe, 'get_doc', return_value=item), \
             patch.object(credit_application, '_locked_context', return_value=(item, frappe._dict(name='A'), frappe._dict(name='P'))), \
             patch.object(frappe.db, 'get_value', return_value='2025-05-15'), \
             patch.object(credit_application, '_save_journal') as save, \
             patch.object(frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
            credit_application.reverse('CREDIT', 'stamp', 'OP', '2025-05-01', 'Corrección')
        save.assert_not_called()

    def test_legacy_offset_reversal_cannot_precede_bank_receipt(self):
        item = self.item(compensations=[frappe._dict(operation_id='OP', amount_usd=10,
            compensation_date='2025-04-30', counterpart='CORE')], save=Mock())
        core = frappe._dict(name='CORE', category='Compensación entre partidas', posting_date='2025-04-30',
            check_permission=Mock(), save=Mock(), compensations=[frappe._dict(operation_id='OP', amount_usd=10,
                compensation_date='2025-04-30', counterpart='CREDIT')])
        with patch.object(frappe, 'get_doc', return_value=item), \
             patch.object(complementary_compensation, '_load_pair', return_value=(item, core)), \
             patch.object(complementary_compensation, '_validate_pair'), \
             patch.object(frappe.db, 'get_value', return_value='2025-05-15'), \
             patch.object(frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
            unwrap(complementary_compensation.reverse_compensation)('CREDIT', 'OP', '2025-05-01', 'Reversión', 'a' * 32)
        item.save.assert_not_called()
        core.save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
