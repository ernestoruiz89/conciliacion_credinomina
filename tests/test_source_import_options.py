"""The source-import form accepts only accounting movements."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
    CNSourceImport,
)
from credinomina_reconciliation.parsers import SOURCE_ACCOUNTING, SourceFileError, parse_source_file


SOURCE_DIR = (
    Path(__file__).resolve().parents[1]
    / "credinomina_reconciliation" / "conciliacion_credinomina"
    / "doctype" / "cn_source_import"
)


class SourceImportOptionsTest(unittest.TestCase):
    def test_metadata_has_one_source(self):
        metadata = json.loads(
            (SOURCE_DIR / "cn_source_import.json").read_text(encoding="utf-8")
        )
        source_type = next(
            field for field in metadata["fields"]
            if field["fieldname"] == "source_type"
        )
        self.assertEqual(source_type["options"], SOURCE_ACCOUNTING)
        self.assertEqual(source_type["default"], SOURCE_ACCOUNTING)
        self.assertEqual(source_type["read_only"], 1)

    def test_form_has_only_accounting_import_action(self):
        script = (SOURCE_DIR / "cn_source_import.js").read_text(encoding="utf-8")
        self.assertIn("3. Cargar movimientos contables", script)
        self.assertNotIn("Transacciones del core", script)
        self.assertNotIn("Detalle de depositos", script)

    def test_other_source_types_are_rejected(self):
        for source_type in ("Transacciones del core (fallback)", "Detalle de depositos", "Movimientos contables (principal)"):
            document = SimpleNamespace(source_type=source_type)
            with patch("frappe.throw", side_effect=ValueError("blocked")):
                with self.assertRaisesRegex(ValueError, "blocked"):
                    CNSourceImport._validate_source_type(document)
            with self.assertRaises(SourceFileError):
                parse_source_file(source_type, "archivo.xlsx", b"")

    def test_accounting_source_is_valid(self):
        CNSourceImport._validate_source_type(SimpleNamespace(source_type=SOURCE_ACCOUNTING))


if __name__ == "__main__":
    unittest.main()
