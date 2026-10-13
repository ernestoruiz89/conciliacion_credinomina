import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.patches.v1_0 import rename_hash_exceptions as migration


class ExceptionNamingMigrationTests(unittest.TestCase):
    def test_only_legacy_hashes_are_selected(self):
        for name in ("62fgjd8vi0", "62fhm959gs", "abcdef1234"):
            self.assertIsNotNone(migration.HASH_NAME.fullmatch(name))
        for name in ("CN-EXC-4-2025-001", "CN-EXC-2026-0001", "Caso documentado", "custom-id"):
            self.assertIsNone(migration.HASH_NAME.fullmatch(name))

    def test_updates_only_exact_exception_id_json_values(self):
        value = '[{"exception_id" : "62fgjd8vi0", "amount_usd":22.52, "description":"62fgjd8vi0", "claim_id":"62fgjd8vi0"}]'
        result = migration.replace_exception_references(value, {"62fgjd8vi0": "CN-EXC-4-2025-001"})
        self.assertEqual(result, value.replace('"exception_id" : "62fgjd8vi0"',
                                             '"exception_id" : "CN-EXC-4-2025-001"'))
        self.assertEqual(migration.replace_exception_references(result, {}), result)
        self.assertEqual(migration.replace_exception_references("[]", {}), "[]")

    def test_migration_keeps_readable_names_and_links_uses_original_creation(self):
        original = frappe._dict(name="62fgjd8vi0", period="P1", creation="2026-09-23 13:10:30")
        readable = frappe._dict(name="CN-EXC-2026-0001", period=None, creation="2026-09-23 13:10:30")
        source = frappe._dict(name="R1", remittance_detail='[{"exception_id":"62fgjd8vi0","importe_usd":25.00}]')
        deposit = frappe._dict(name="D1", allocation_detail='[{"exception_id":"62fgjd8vi0","importe_usd":25.00}]')
        with patch.object(migration.frappe, "db", Mock()) as db, \
                patch.object(migration.frappe, "get_meta", return_value=Mock()), \
                patch.object(migration.frappe, "get_all", side_effect=[[original, readable], [source], [deposit]]), \
                patch.object(migration, "exception_name_prefix") as prefix, \
                patch.object(migration, "new_exception_name", return_value="CN-EXC-4-2025-001") as naming, \
                patch.object(migration.frappe, "rename_doc") as rename, \
                patch.object(migration.frappe, "clear_cache"):
            migration.execute()
            prefix.assert_called_once_with("P1", original.creation)
            naming.assert_called_once_with("P1", original.creation)
            rename.assert_called_once_with(migration.DOCTYPE, original.name, "CN-EXC-4-2025-001",
                                           force=True, merge=False, show_alert=False, rebuild_search=False)
            db.set_value.assert_any_call(migration.DOCTYPE, original.name, "legacy_name", original.name,
                                         update_modified=False)
            db.set_value.assert_any_call("CN Collection Row", "R1", "remittance_detail",
                '[{"exception_id":"CN-EXC-4-2025-001","importe_usd":25.00}]', update_modified=False)
            db.set_value.assert_any_call("CN Remittance Allocation", "D1", "allocation_detail",
                '[{"exception_id":"CN-EXC-4-2025-001","importe_usd":25.00}]', update_modified=False)
            self.assertEqual(db.set_value.call_count, 3)

    def test_prevalidates_every_period_before_allocating_series_or_renaming(self):
        rows = [frappe._dict(name="62fgjd8vi0", period="P1", creation="2026-09-23"),
                frappe._dict(name="62fhm959gs", period="Missing", creation="2026-09-23")]
        with patch.object(migration.frappe, "db", Mock()) as db, \
                patch.object(migration.frappe, "get_meta", return_value=Mock()), \
                patch.object(migration.frappe, "get_all", return_value=rows), \
                patch.object(migration, "exception_name_prefix", side_effect=[("CN-EXC-4-2025-", 3), ValueError]), \
                patch.object(migration, "new_exception_name") as naming, \
                patch.object(migration.frappe, "rename_doc") as rename:
            with self.assertRaises(ValueError):
                migration.execute()
            naming.assert_not_called()
            rename.assert_not_called()
            db.set_value.assert_not_called()

    def test_repeated_migration_without_hashes_does_not_consume_series_or_write(self):
        with patch.object(migration.frappe, "db", Mock()) as db, \
                patch.object(migration.frappe, "get_meta", return_value=Mock()), \
                patch.object(migration.frappe, "get_all", return_value=[
                    SimpleNamespace(name="CN-EXC-4-2025-001", period="P1", creation="2026-09-23")]), \
                patch.object(migration, "new_exception_name") as naming, \
                patch.object(migration.frappe, "rename_doc") as rename:
            migration.execute()
            naming.assert_not_called()
            rename.assert_not_called()
            db.set_value.assert_not_called()

    def test_missing_table_does_nothing(self):
        with patch.object(migration.frappe, "db", Mock(table_exists=Mock(return_value=False))), \
                patch.object(migration.frappe, "get_all") as records:
            migration.execute()
            records.assert_not_called()

    def test_schema_preserves_searchable_hash_without_copying_it(self):
        root = Path(migration.__file__).parents[2]
        schema = json.loads((root / "conciliacion_credinomina/doctype/cn_reconciliation_exception/cn_reconciliation_exception.json").read_text(encoding="utf-8"))
        field = next(field for field in schema["fields"] if field["fieldname"] == "legacy_name")
        self.assertTrue(field["read_only"])
        self.assertTrue(field["no_copy"])
        self.assertIn("legacy_name", schema["search_fields"])
        self.assertIn("credinomina_reconciliation.patches.v1_0.rename_hash_exceptions",
                      (root / "patches.txt").read_text(encoding="utf-8"))
