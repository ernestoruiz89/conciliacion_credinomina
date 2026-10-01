import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation import accounting_naming as naming
from credinomina_reconciliation.patches.v1_0 import rename_source_import_doctype as migration


class AccountingNamingTests(unittest.TestCase):
    def test_month_comes_from_earliest_movement_not_row_order(self):
        self.assertEqual(naming.accounting_month([
            {"event_date": "2026-10-15"}, {"event_date": None},
            {"event_date": "2026-09-30"}, {"event_date": "2026-09-01"},
        ]), date(2026, 9, 1))
        self.assertIsNone(naming.accounting_month([{"event_date": None}]))

    def test_literal_company_code_and_three_digit_counter(self):
        with patch.object(naming.frappe, "db", Mock(
            get_value=Mock(return_value="5111.A"), exists=Mock(return_value=False),
        )), patch.object(naming, "validate_name"), \
             patch.object(naming, "getseries", return_value="001") as counter:
            prefix = naming.accounting_prefix("Empresa A", date(2026, 9, 1))
            self.assertEqual(naming.new_accounting_name(prefix), "CONTA-5111.A-9-2026-001")
            counter.assert_called_once_with("CONTA-5111.A-9-2026-", 3)

    def test_missing_date_or_company_uses_provisional_name(self):
        self.assertIsNone(naming.accounting_prefix("Empresa", None))
        self.assertIsNone(naming.accounting_prefix(None, date(2026, 9, 1)))
        with patch.object(naming, "make_autoname", return_value="DRAFT") as draft:
            self.assertEqual(naming.new_accounting_name(None), "DRAFT")
            draft.assert_called_once_with("CONTA-BORRADOR-.YYYY.-.#####")

    def test_skip_existing_identifiers_and_recognize_assigned_name(self):
        with patch.object(naming.frappe, "db", Mock(exists=Mock(side_effect=[True, False]))), \
             patch.object(naming, "getseries", side_effect=["001", "002"]):
            self.assertEqual(naming.new_accounting_name("CONTA-5111-9-2026-"), "CONTA-5111-9-2026-002")
        self.assertTrue(naming.matches_accounting_prefix("CONTA-5111-9-2026-1000", "CONTA-5111-9-2026-"))
        self.assertFalse(naming.matches_accounting_prefix("CONTA-5111-10-2026-001", "CONTA-5111-9-2026-"))

    def test_rename_updates_in_memory_children_and_route_once(self):
        child = SimpleNamespace(parent="OLD")
        document = Mock(name="document")
        document.name = "OLD"
        document.get.side_effect = lambda field: {"rows": [{"event_date": "2026-09-15"}], "employer": "A"}[field]
        document.get_all_children.return_value = [child]
        with patch.object(naming, "accounting_prefix", return_value="CONTA-5111-9-2026-"), \
             patch.object(naming, "new_accounting_name", return_value="CONTA-5111-9-2026-001"), \
             patch.object(naming.frappe, "rename_doc", return_value="CONTA-5111-9-2026-001") as rename:
            naming.rename_accounting_import(document)
            self.assertEqual(document.localname, "OLD")
            self.assertEqual(child.parent, document.name)
            naming.rename_accounting_import(document)
            self.assertEqual(rename.call_count, 1)

    def test_doctype_migration_is_idempotent_and_blocks_conflicts(self):
        with patch.object(migration.frappe, "db", Mock(exists=Mock(return_value=False))), \
             patch.object(migration.frappe, "rename_doc") as rename:
            migration.execute()
            rename.assert_not_called()
        with patch.object(migration.frappe, "db", Mock(exists=Mock(side_effect=[True, True]))), \
             patch.object(migration, "_", side_effect=lambda text: text), \
             patch.object(migration.frappe, "throw", side_effect=ValueError), \
             patch.object(migration.frappe, "rename_doc") as rename:
            with self.assertRaises(ValueError):
                migration.execute()
            rename.assert_not_called()
