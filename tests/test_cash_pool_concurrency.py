import unittest
from unittest.mock import Mock, patch
from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
import frappe


class CashPoolConcurrencyTests(unittest.TestCase):
    def test_stale_repeatable_read_is_rejected_before_any_distribution(self):
        database = Mock(sql=Mock(side_effect=[[{"name": "A", "reconciliation_revision": 4}], [{"name": "A", "reconciliation_revision": 5}]]))
        with patch.object(frappe, "db", database), patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
            lock_cash_pool(["A"])
        self.assertEqual(database.sql.call_count, 2)

    def test_lock_order_and_generation_share_transaction(self):
        rows = [{"name": "A", "reconciliation_revision": 4}, {"name": "B", "reconciliation_revision": 6}]
        database = Mock(sql=Mock(side_effect=[rows, rows, None]))
        with patch.object(frappe, "db", database):
            lock_cash_pool(["B", "A"])
        self.assertIn("FOR UPDATE", database.sql.call_args_list[1].args[0])
        self.assertEqual(database.sql.call_args_list[1].args[1]["companies"], ("A", "B"))
        self.assertIn("+1", database.sql.call_args_list[2].args[0])
        database.commit.assert_not_called()
