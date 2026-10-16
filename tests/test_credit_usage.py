import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.credit_usage import totals
from credinomina_reconciliation.patches.v1_0 import include_compensations_in_credit_usage as migration


class CreditUsageTests(unittest.TestCase):
    def item(self, **changes):
        return frappe._dict(dict(name='C', amount_usd=32.63,
            credit_history='[{"fecha":"2025-05-15","importe_usd":14.17}]',
            compensations=[dict(compensation_date='2025-06-01', amount_usd=18.46)]) | changes)

    def test_migration_repairs_existing_cache_idempotently_without_changing_journals(self):
        item = self.item(credit_pending_usd=18.46, credit_resolved_usd=14.17)
        with patch.object(frappe, 'get_all', return_value=['C']), \
                patch.object(frappe, 'get_doc', return_value=item), \
                patch.object(frappe, 'get_meta', return_value=Mock()), \
                patch.object(frappe.db, 'set_value') as save, patch.object(frappe, 'clear_document_cache'):
            migration.execute()
            migration.execute()
        self.assertEqual(save.call_count, 2)
        self.assertEqual(save.call_args.args, ('CN Complementary Item', 'C', dict(
            credit_resolved_usd=32.63, credit_pending_usd=0, credit_management_status='Resuelto', credit_usage_issue='')))

    def test_migration_preserves_ambiguous_histories_and_marks_unavailable_for_review(self):
        invalid = self.item(name='BAD', credit_history='[{"importe_usd":32.63}]')
        with patch.object(frappe, 'get_all', return_value=['C', 'BAD']), \
                patch.object(frappe, 'get_doc', side_effect=[self.item(), invalid]), \
                patch.object(frappe, 'get_meta', return_value=Mock()), \
                patch.object(frappe, 'clear_document_cache'), patch.object(frappe, 'log_error') as log, \
                patch.object(frappe, 'throw', side_effect=lambda message: (_ for _ in ()).throw(frappe.ValidationError(message))), \
                patch.object(frappe.db, 'set_value') as save:
            migration.execute()
        self.assertEqual(save.call_count, 2)
        values = save.call_args.args[2]
        self.assertEqual(values['credit_pending_usd'], 0)
        self.assertEqual(values['credit_management_status'], 'Revisar vínculo')
        self.assertTrue(values['credit_usage_issue'])
        self.assertNotIn('credit_history', values)
        self.assertNotIn('credit_resolved_usd', values)
        log.assert_called_once()

    def test_combined_timeline_rejects_intermediate_overuse_even_when_current_balance_is_valid(self):
        item = self.item(compensations=[dict(compensation_date='2025-05-01', amount_usd=32.63),
                                      dict(compensation_date='2025-06-01', amount_usd=-32.63)])
        with patch.object(frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
            totals(item)

    def test_compensating_again_after_reversal_is_not_a_second_use(self):
        item = self.item(compensations=[dict(compensation_date=day, amount_usd=amount) for day, amount in [
            ('2025-06-01', 18.46), ('2025-07-01', -18.46), ('2025-08-01', 18.46)]])
        self.assertEqual(totals(item)['credit_pending_usd'], 0)
