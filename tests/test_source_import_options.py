"""New source imports are core applications, not bank-deposit files."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
    CNSourceImport,
)
from credinomina_reconciliation.parsers import SOURCE_ACCOUNTING, SOURCE_DEPOSITS


SOURCE_DIR = (
    Path(__file__).resolve().parents[1]
    / "credinomina_reconciliation" / "conciliacion_credinomina"
    / "doctype" / "cn_source_import"
)


class SourceImportOptionsTest(unittest.TestCase):
    def test_legacy_value_stays_in_metadata_for_existing_documents(self):
        metadata = json.loads(
            (SOURCE_DIR / "cn_source_import.json").read_text(encoding="utf-8")
        )
        source_type = next(
            field for field in metadata["fields"]
            if field["fieldname"] == "source_type"
        )
        self.assertIn(SOURCE_DEPOSITS, source_type["options"].split("\n"))

    def test_form_limits_new_choices_and_hides_legacy_import_action(self):
        script = (SOURCE_DIR / "cn_source_import.js").read_text(encoding="utf-8")
        self.assertIn('frm.set_df_property("source_type", "options"', script)
        self.assertIn('frm.set_df_property("source_type", "read_only"', script)
        self.assertIn("if (!legacyDeposits)", script)
        self.assertNotIn("Importar depósitos antiguos", script)

    def test_new_deposit_source_is_rejected(self):
        document = SimpleNamespace(
            doctype="CN Source Import", name="NEW-1", source_type=SOURCE_DEPOSITS,
        )
        with (
            patch(
                "credinomina_reconciliation.conciliacion_credinomina.doctype."
                "cn_source_import.cn_source_import.frappe.db",
                new=SimpleNamespace(get_value=Mock(return_value=None)),
            ),
            patch("frappe.throw", side_effect=ValueError("blocked")) as throw,
        ):
            with self.assertRaisesRegex(ValueError, "blocked"):
                CNSourceImport._validate_source_type(document)
        throw.assert_called_once()

    def test_existing_deposit_source_remains_valid_for_reconciliation(self):
        document = SimpleNamespace(
            doctype="CN Source Import", name="OLD-1", source_type=SOURCE_DEPOSITS,
        )
        with patch(
            "credinomina_reconciliation.conciliacion_credinomina.doctype."
            "cn_source_import.cn_source_import.frappe.db",
            new=SimpleNamespace(get_value=Mock(return_value=SOURCE_DEPOSITS)),
        ):
            CNSourceImport._validate_source_type(document)

    def test_application_source_is_unchanged(self):
        document = SimpleNamespace(
            doctype="CN Source Import", name="APP-1", source_type=SOURCE_ACCOUNTING,
        )
        CNSourceImport._validate_source_type(document)


if __name__ == "__main__":
    unittest.main()
