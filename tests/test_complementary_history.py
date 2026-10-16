import json
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import complementary_history as history, client_credit


class HistoryDeletionTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(frappe, 'throw', side_effect=lambda message: (_ for _ in ()).throw(ValueError(message)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_corrected_compensation_is_one_connected_family_from_any_entry(self):
        rows = [dict(operation_id='A'), dict(operation_id='B', reverses_operation_id='A'),
                dict(operation_id='C', reverses_operation_id='A', corrects_operation_id='A'),
                dict(operation_id='D', reverses_operation_id='B', corrects_operation_id='A'),
                dict(operation_id='E', corrects_operation_id='A'), dict(operation_id='OTHER')]
        for entry in rows[:5]:
            self.assertEqual(len(history._group(rows, 'compensation', entry['operation_id'])), 5)

    def test_missing_related_entry_or_duplicate_id_blocks_deletion(self):
        for rows in ([dict(entry_id='R', reverses='MISSING')], [dict(entry_id='R'), dict(entry_id='R')]):
            with self.assertRaises(ValueError):
                history._group(rows, 'management', 'R')

    def item(self, **changes):
        return frappe._dict(dict(name='C', doctype='CN Complementary Item', docstatus=1,
            category=client_credit.CATEGORY, result=client_credit.RESULT, modified='v1', amount_usd=10,
            compensations=[], credit_history='[]', check_permission=Mock(), clear_cache=Mock(), add_comment=Mock()) | changes)

    def test_management_deletion_preserves_legacy_ids_after_reindexing(self):
        rows = [dict(importe_usd=3), dict(importe_usd=4)]
        retained_id = client_credit.management_entry_id(rows[1], 1)
        rows.append(dict(operation_id='R', reverses=retained_id, importe_usd=-4))
        item = self.item(credit_history=json.dumps(rows))
        removed_id = client_credit.management_entry_id(rows[0], 0)
        with patch.object(frappe, 'get_doc', return_value=item):
            _, _, rest, _, preview = history._prepare('C', 'management', removed_id)
        item.credit_history = json.dumps(rest[0])
        self.assertEqual(rest[0][0]['operation_id'], retained_id)
        self.assertEqual([row['can_reverse'] for row in client_credit.management_history(item)], [False, False])
        self.assertEqual(preview['changes'][0]['credit_pending_usd'], 10)

    def test_deleting_original_management_also_removes_reversal_and_keeps_other_uses(self):
        item = self.item(credit_history=json.dumps([dict(operation_id='A', importe_usd=6),
            dict(operation_id='B', importe_usd=-6, reverses='A'), dict(operation_id='C', importe_usd=3)]))
        with patch.object(frappe, 'get_doc', return_value=item):
            _, groups, remaining, _, preview = history._prepare('C', 'management', 'B')
        self.assertEqual(len(groups[0]), 2)
        self.assertEqual([row['operation_id'] for row in remaining[0]], ['C'])
        self.assertEqual(preview['changes'][0]['credit_pending_usd'], 7)

    def test_preview_allows_repairing_invalid_current_usage_but_not_invalid_remaining_history(self):
        item = self.item(credit_history=json.dumps([dict(operation_id='A', importe_usd=10), dict(operation_id='B', importe_usd=10)]))
        with patch.object(frappe, 'get_doc', return_value=item):
            preview = history.preview_deletion('C', 'management', 'A')
            self.assertEqual(preview['changes'][0]['credit_pending_usd'], 0)
            item.compensations = [frappe._dict(compensation_date='2025-01-01', amount_usd=1)]
            with self.assertRaises(ValueError):
                history.preview_deletion('C', 'management', 'A')

    def test_permissions_and_cancelled_items_are_checked_before_exposing_history(self):
        item = self.item()
        with patch.object(frappe, 'get_doc', return_value=item):
            item.check_permission.side_effect = PermissionError
            with self.assertRaises(PermissionError):
                history.deletion_options('C', 'management')
            item.check_permission.side_effect = None
            item.docstatus = 2
            with self.assertRaises(ValueError):
                history.deletion_options('C', 'management')

    def test_compensation_preview_checks_both_halves_and_counterpart_version(self):
        left = self.item(posting_date='2025-01-01', compensations=[frappe._dict(
            operation_id='A', counterpart='R', compensation_date='2025-02-01', amount_usd=10)])
        right = self.item(name='R', category='Compensación entre partidas', posting_date='2025-01-01',
            compensations=[frappe._dict(operation_id='A', counterpart='C', compensation_date='2025-02-01', amount_usd=10)])
        with patch.object(frappe, 'get_doc', return_value=left), \
                patch.object(history.offsets, '_load_pair', return_value=(left, right)), \
                patch.object(history.offsets, '_validate_pair'):
            preview = history.preview_deletion('C', 'compensation', 'A')
            self.assertEqual(preview['changes'][0]['credit_pending_usd'], 10)
            self.assertEqual(preview['changes'][1]['compensated_usd'], 0)
            right.modified = 'v2'
            self.assertNotEqual(preview['token'], history.preview_deletion('C', 'compensation', 'A')['token'])
            right.compensations[0].amount_usd = 9
            with self.assertRaises(ValueError):
                history.preview_deletion('C', 'compensation', 'A')
    def test_stale_preview_and_missing_reason_cannot_write(self):
        with patch.object(history, '_prepare', return_value=([], [], [], None, dict(token='new'))), \
                patch.object(frappe.db, 'set_value') as save:
            with self.assertRaises(ValueError):
                history.delete_history('C', 'management', 'A', 'old', 'Corrección')
            with self.assertRaises(ValueError):
                history.delete_history('C', 'management', 'A', 'new', '')
        save.assert_not_called()

    def test_period_rebuild_failure_rolls_back_management_and_audit(self):
        item, period = self.item(), frappe._dict(name='PERIOD')
        with patch.object(history, '_prepare', return_value=([item], [[dict(operation_id='A')]], [[]], period, dict(token='ok'))), \
                patch.object(frappe.db, 'savepoint'), patch.object(frappe.db, 'set_value') as save, \
                patch.object(frappe.db, 'rollback') as rollback, \
                patch.object(history.credit_application, 'rebuild_destination', side_effect=ValueError('rebuild')):
            with self.assertRaises(ValueError):
                history.delete_history('C', 'management', 'A', 'ok', 'Corrección')
            save.assert_called_once()
            rollback.assert_called_once_with(save_point='delete_complementary_history')
