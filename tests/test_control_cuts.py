import io
import json
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import frappe
from openpyxl import load_workbook

from credinomina_reconciliation import control_cuts
from credinomina_reconciliation.control_export import build_control_workbook


class ControlCutTests(unittest.TestCase):
    def test_read_requires_period_permission_before_history_query(self):
        period = Mock()
        period.check_permission.side_effect = ValueError("Forbidden")
        with patch.object(frappe, "get_doc", return_value=period), patch.object(frappe, "get_all") as query:
            with self.assertRaises(ValueError):
                control_cuts.get_cuts("P")
        query.assert_not_called()

    def test_cut_files_are_private_and_version_keeps_hashes(self):
        period = frappe._dict(name="P", doctype="CN Reconciliation Period", employer="E", payroll_month="2025-04-01",
            control_cut_on=datetime(2026, 10, 3), control_cut_note="Check", control_cut_summary="Partial")
        period.as_dict = lambda: {"name": "P", "applied_usd": 10}
        data = {"periods": [{"name": "P", "exceptions": []}]}
        saved = []
        def save(name, content, doctype, docname, **options):
            self.assertEqual((doctype, docname, options["is_private"]), ("CN Reconciliation Period", "P", 1))
            saved.append(content)
            return frappe._dict(name=name, file_url="/private/files/" + name)
        record = Mock(name="V")
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(frappe, "session", frappe._dict(user="operator")), \
             patch.object(frappe, "generate_hash", return_value="new-cut"), \
             patch.object(frappe, "db", Mock(get_single_value=Mock(return_value="dd/mm/yyyy"))), \
             patch.object(frappe, "get_doc", return_value=record) as get_doc, \
             patch.object(control_cuts, "save_file", side_effect=save), \
             patch.object(control_cuts, "build_control_workbook", return_value=b"XLSX"), \
             patch("credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina._build_control_data", return_value=data), \
             patch("credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos.antiguedad_de_saldos.execute", return_value=([], [{"period": "P"}, {"period": "OTHER"}])):
            result = control_cuts.save_cut(period)
        snapshot = json.loads(saved[0])
        self.assertEqual(snapshot["control_data"]["aging_rows"], [{"period": "P"}])
        self.assertEqual(snapshot["recorded_by"], "operator")
        self.assertEqual(len(result["files"]["json"]["sha256"]), 64)
        self.assertEqual(get_doc.call_args.args[0]["doctype"], "Version")
        record.insert.assert_called_once_with(ignore_permissions=True)

    def test_snapshot_workbook_contains_values_not_live_formulas(self):
        content = build_control_workbook({"periods": [], "aging_rows": [{"client_name": "=evil()", "amount_usd": 12.34,
            "due_date": "2025-05-10", "payment_term_origin": "Migrated"}]}, exceptions=[], actions=[], employer_label="E",
            generated_at=datetime(2026, 10, 3))
        book = load_workbook(io.BytesIO(content))
        sheet = book["Antigüedad guardada"]
        self.assertTrue(any(cell.value == 12.34 for row in sheet for cell in row))
        self.assertFalse(any(cell.data_type == "f" for row in sheet for cell in row))
        book.close()
