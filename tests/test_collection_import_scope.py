"""Uploading one payroll never asks to reconcile the company's other periods."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as api


class Period(frappe._dict):
    def append(self, field, values):
        row = frappe._dict(values)
        self[field].append(row)
        return row

    def set(self, field, value):
        self[field] = value


class CollectionImportScopeTests(unittest.TestCase):
    def test_new_corrected_and_unchanged_uploads_leave_other_periods_out_of_scope(self):
        for state in ("new", "corrected", "unchanged"):
            with self.subTest(state=state):
                content = b"collection"
                record = dict(source_row=2, row_key="KEY", client_number="CLIENT", client_name="Cliente",
                              loan_number="123-1", expected_usd=80)
                existing = frappe._dict(record, name="ROW", client="CLIENT", expected_usd=50)
                period = Period(name="P", employer="E", reconciliation_mode="Operativa", status="Borrador",
                    payroll_month="2026-09-01", collection_cycle="Segunda quincena", cutoff_date="2026-09-30",
                    collection_file="/private/files/collection.csv", notes="", flags=frappe._dict(),
                    collection_import_sha256=api.file_sha256(content) if state == "unchanged" else "old-hash",
                    collection_rows=[] if state == "new" else [existing], check_permission=Mock(),
                    save=Mock(), reload=Mock())
                clients = Mock()
                clients.ensure_from_collection.return_value = "CLIENT"

                def reconcile(employer, *, period_name=None, **kwargs):
                    if period_name != period.name:
                        raise frappe.ValidationError("An unrelated application has reserved cash")
                    return {"period": period_name, "rows": 0}

                with patch.object(api.frappe, "get_doc", return_value=period), \
                     patch.object(api.frappe, "db", SimpleNamespace(exists=lambda *_: True)), \
                     patch.object(api, "_attached_file", return_value=(frappe._dict(file_name="collection.csv"), content)), \
                     patch.object(api, "parse_collection_file", return_value=[record]), \
                     patch.object(api, "enrich_collection_records", return_value={"completed_rows": 0}), \
                     patch.object(api, "ClientIndex", return_value=clients), \
                     patch("credinomina_reconciliation.paying_employers.reconciliation_companies", return_value=["E"]), \
                     patch.object(engine, "_reconcile_sources", side_effect=reconcile) as call:
                    result = api.import_collection(period.name)

                self.assertEqual(result["source_reconciliation"]["period"], "P")
                self.assertEqual(result["rows"], 1)
                call.assert_called_once_with("E", progress=None, preserve_deposits=True, period_name="P")
                if state == "unchanged":
                    self.assertTrue(result["unchanged"])
                    period.save.assert_not_called()
                else:
                    period.save.assert_called_once()
                    self.assertEqual(period.collection_rows[0].expected_usd, 80)
                if state != "new":
                    self.assertEqual(period.collection_rows[0].name, "ROW")

    def test_no_existing_sources_does_not_start_reconciliation(self):
        with patch.object(api.frappe, "db", SimpleNamespace(exists=lambda *_: False)), \
             patch("credinomina_reconciliation.paying_employers.reconciliation_companies", return_value=["E"]), \
             patch.object(engine, "_reconcile_sources") as call:
            self.assertIsNone(api._reconcile_if_sources("E", period_name="P"))
        call.assert_not_called()
