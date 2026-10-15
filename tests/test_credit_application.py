import json
import unittest
from unittest.mock import Mock, patch
from inspect import unwrap

import frappe
from credinomina_reconciliation import credit_application as service


class CreditApplicationTests(unittest.TestCase):
    def setUp(self):
        self.entry = dict(operation_id='OP', application_id='APP', importe_usd=20.76,
                          fecha='2026-05-30', referencia='V', period='MAY')
        self.item = frappe._dict(name='CREDIT', doctype='CN Complementary Item', docstatus=1,
            category=service.CATEGORY, result=service.RESULT, registered_deposit='DEP', employer='EMP',
            credit_client='CLIENT', client_number='2885', loan_number='11125-1', amount_usd=20.77,
            credit_resolved_usd=0, credit_pending_usd=20.77, credit_history='[]', posting_date='2026-04-30',
            check_permission=Mock(), clear_cache=Mock(), add_comment=Mock())
        self.row = frappe._dict(name='APP', parent='IMPORT', event_date='2026-05-30',
                               client='CLIENT', client_number='2885', loan_number='0011125-1', voucher='V')

    def test_identity_uses_client_company_and_canonical_loan(self):
        self.assertTrue(service._identity_matches(self.item, self.row, 'EMP'))
        for row, company in [(self.row, 'OTHER'), (dict(self.row, client='OTHER'), 'EMP'),
                             (dict(self.row, loan_number='999-1'), 'EMP')]:
            self.assertFalse(service._identity_matches(self.item, row, company))

    def test_reversal_is_dated_and_preserves_the_original_operation(self):
        history = [self.entry, dict(operation_id='REV', reverses='OP', application_id='APP',
                                   importe_usd=-20.76, fecha='2026-06-01')]
        self.item.credit_history = json.dumps(history)
        self.assertEqual(service.journal_allocations([self.item], {'APP'}, '2026-05-29'), [])
        before = service.journal_allocations([self.item], {'APP'}, '2026-05-30')
        self.assertEqual(before[0]['importe_usd'], 20.76)
        self.assertEqual(service.journal_allocations([self.item], {'APP'}, '2026-06-01'), [])
        self.assertEqual(json.loads(self.item.credit_history), history)

    def test_balance_projection_does_not_mutate_cash_allocations(self):
        allocation = dict(allocations=[], deposit_meta={}, registered_ids={'DEP': 'DEP'})
        entry = dict(self.entry, item='CREDIT', deposit='DEP', employer='EMP')
        with patch.object(service, 'load_allocations', return_value=[entry]):
            projected = service.with_funding(allocation, [frappe._dict(name='APP', historical_period='MAY')])
        self.assertEqual(allocation, dict(allocations=[], deposit_meta={}, registered_ids={'DEP': 'DEP'}))
        self.assertEqual(projected['allocations'][0]['amount_usd'], 20.76)
        self.assertEqual(projected['allocations'][0]['origin_deposit'], 'DEP')
        self.assertEqual(projected['registered_ids'], {'DEP': 'DEP'})

    def test_apply_retains_remainder_and_rebuilds_only_destination(self):
        period = frappe._dict(name='MAY')
        with patch.object(service, '_locked_context', return_value=(self.item, self.row, period)), \
                patch('credinomina_reconciliation.application_adjustments.cash_coverage', return_value={'adjustable_usd': 20.76}), \
                patch.object(service, 'rebuild_destination') as rebuild, \
                patch.object(service.frappe.db, 'set_value') as save, \
                patch.object(service.frappe.db, 'get_value', return_value=0):
            result = unwrap(service.apply)('CREDIT', 'stamp', 'APP', 20.76, '2026-05-30')
        self.assertEqual(result['pending_usd'], .01)
        self.assertEqual(result['application_pending_usd'], 0)
        rebuild.assert_called_once_with(period)
        values = save.call_args.args[2]
        self.assertEqual(values['credit_resolved_usd'], 20.76)
        self.assertEqual(values['credit_management_status'], 'Parcialmente resuelto')
        self.assertEqual(json.loads(values['credit_history'])[0]['application_id'], 'APP')

    def test_rejects_overpayment_invalid_dates_and_nonpositive_amounts_before_writes(self):
        with patch.object(service, '_locked_context', return_value=(self.item, self.row, frappe._dict(name='MAY'))), \
                patch('credinomina_reconciliation.application_adjustments.cash_coverage', return_value={'adjustable_usd': 20.76}), \
                patch.object(service, '_save_journal') as save, \
                patch.object(service.frappe, 'throw', side_effect=ValueError):
            for amount, date in [(0, '2026-05-30'), (-1, '2026-05-30'), (21, '2026-05-30'),
                                 (20.77, '2026-05-30'), (20, '2026-05-29'), (20, '2099-01-01')]:
                with self.subTest(amount=amount, date=date), self.assertRaises(ValueError):
                    unwrap(service.apply)('CREDIT', 'stamp', 'APP', amount, date)
            save.assert_not_called()

    def test_corrupt_journal_cannot_release_credit(self):
        self.item.credit_resolved_usd = 10
        with patch.object(service.frappe, 'throw', side_effect=ValueError), \
                patch.object(service.frappe.db, 'set_value') as save, self.assertRaises(ValueError):
            service._save_journal(self.item, self.entry)
        save.assert_not_called()

    def test_backdating_cannot_reuse_credit_before_its_reversal(self):
        history = [dict(fecha='2026-05-30', importe_usd=20.76),
                   dict(fecha='2026-06-01', importe_usd=-20.76),
                   dict(fecha='2026-05-31', importe_usd=20.76)]
        with patch.object(service.frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
            service.validate_dated_history(20.77, history)
        history[-1]['fecha'] = '2026-06-02'
        service.validate_dated_history(20.77, history)


if __name__ == '__main__':
    unittest.main()
