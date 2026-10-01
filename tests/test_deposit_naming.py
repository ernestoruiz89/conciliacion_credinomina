import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation import deposit_naming as naming
from credinomina_reconciliation.patches.v1_0 import rename_deposits_by_date as migration


class DepositNamingTests(unittest.TestCase):
    def test_uses_deposit_month_year_and_four_digits(self):
        with patch.object(naming.frappe, "db", Mock(exists=Mock(return_value=False))), \
             patch.object(naming, "getseries", return_value="0001") as counter:
            self.assertEqual(naming.new_deposit_name("2025-04-23"), "DEP-4-2025-0001")
            counter.assert_called_once_with("DEP-4-2025-", 4)

    def test_skips_occupied_names(self):
        with patch.object(naming.frappe, "db", Mock(exists=Mock(side_effect=[True, False]))), \
             patch.object(naming, "getseries", side_effect=["0001", "0002"]):
            self.assertEqual(naming.new_deposit_name("2026-09-01"), "DEP-9-2026-0002")

    def test_missing_date_never_defaults_to_today(self):
        with patch.object(naming, "_", side_effect=lambda s: s), \
             patch.object(naming.frappe, "throw", side_effect=ValueError), \
             patch.object(naming, "getseries") as counter:
            with self.assertRaises(ValueError):
                naming.new_deposit_name(None)
            counter.assert_not_called()

    def test_names_already_assigned_are_preserved(self):
        for name in ("DEP-9-2026-0001", "DEP-12-2025-10000"):
            self.assertTrue(naming.uses_deposit_series(name))
        for name in ("CN-ALLOC-2026-00001", "DEP-09-2026-0001", "DEP-0-2026-0001"):
            self.assertFalse(naming.uses_deposit_series(name))

    def test_migration_prevalidates_all_dates_before_renaming(self):
        rows = [SimpleNamespace(name="OLD1", deposit_date="2025-04-01"),
                SimpleNamespace(name="OLD2", deposit_date=None)]
        with patch.object(migration.frappe, "db", Mock()), \
             patch.object(migration.frappe, "get_meta", return_value=Mock()), \
             patch.object(migration.frappe, "get_all", return_value=rows), \
             patch.object(migration, "_", side_effect=lambda s: s), \
             patch.object(migration.frappe, "throw", side_effect=ValueError), \
             patch.object(migration.frappe, "rename_doc") as rename:
            with self.assertRaises(ValueError):
                migration.execute()
            rename.assert_not_called()
