"""The source-import form accepts only accounting movements."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as source
from credinomina_reconciliation.parsers import SourceFileError, parse_source_file


SOURCE_DIR = (
    Path(__file__).resolve().parents[1]
    / "credinomina_reconciliation" / "conciliacion_credinomina"
    / "doctype" / "cn_accounting_import"
)


class SourceImportOptionsTest(unittest.TestCase):
    def test_metadata_does_not_require_a_source_type(self):
        metadata = json.loads(
            (SOURCE_DIR / "cn_accounting_import.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("source_type", metadata["field_order"])
        self.assertNotIn("source_type", {field["fieldname"] for field in metadata["fields"]})
        self.assertNotIn("manual_fx_evidence", metadata["field_order"])
        self.assertNotIn(
            "manual_fx_evidence", {field["fieldname"] for field in metadata["fields"]}
        )
        row_metadata = json.loads(
            (SOURCE_DIR.parent / "cn_source_row" / "cn_source_row.json")
            .read_text(encoding="utf-8")
        )
        self.assertNotIn("manual_fx_evidence", row_metadata["field_order"])
        self.assertNotIn(
            "manual_fx_evidence",
            {field["fieldname"] for field in row_metadata["fields"]},
        )

    def test_form_has_only_accounting_import_action(self):
        script = (SOURCE_DIR / "cn_accounting_import.js").read_text(encoding="utf-8")
        self.assertIn("3. Cargar movimientos contables", script)
        self.assertNotIn("Transacciones del core", script)
        self.assertNotIn("Detalle de depositos", script)

    def test_other_source_types_are_rejected(self):
        for source_type in ("Transacciones del core (fallback)", "Detalle de depositos", "Movimientos contables (principal)"):
            with self.assertRaises(SourceFileError):
                parse_source_file(source_type, "archivo.xlsx", b"")

    def test_import_uses_accounting_parser_without_source_type(self):
        document = SimpleNamespace(name="IMPORT", employer="Empresa", currency="USD",
            manual_fx_rate=0, portfolio_snapshot=None, historical_period=None, rows=[],
            check_permission=Mock(), set=Mock(), append=Mock(), save=Mock())
        records = [{"source_key": "movement", "event_type": "Aplicacion"}]
        with (
            patch.object(source.frappe, "get_doc", return_value=document),
            patch.object(source.frappe, "session", SimpleNamespace(user="Administrator")),
            patch.object(source, "now_datetime", return_value="2026-09-30 12:00:00"),
            patch.object(source, "_", side_effect=lambda text: text),
            patch.object(source, "_attached_file", return_value=(SimpleNamespace(file_name="movimientos.xlsx"), b"data")),
            patch.object(source, "parse_accounting_movements", return_value=records) as parser,
            patch.object(source, "apply_accounting_currency_override", return_value=records),
            patch.object(source, "enrich_accounting_records", return_value=records),
            patch.object(source, "enrich_source_import_clients", return_value=records),
            patch.object(source, "reconcile_all_sources", return_value={"rows": 1}),
        ):
            result = source.import_source_file("IMPORT")
        parser.assert_called_once_with("movimientos.xlsx", b"data")
        document.check_permission.assert_called_once_with("write")
        document.append.assert_called_once()
        document.save.assert_called_once()
        self.assertEqual(result["import_name"], "IMPORT")

    def test_duplicate_detection_does_not_query_removed_field(self):
        document = SimpleNamespace(doctype="CN Accounting Import", name="IMPORT", file_hash="hash")
        with patch.object(source.frappe, "db", Mock(get_value=Mock(return_value=None))) as database:
            source.CNAccountingImport._validate_duplicate_file(document)
        filters = database.get_value.call_args.args[1]
        self.assertEqual(filters["file_hash"], "hash")
        self.assertNotIn("source_type", filters)


if __name__ == "__main__":
    unittest.main()
