import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_identity import identify_lines
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting


class AccountingLineIdentityTests(unittest.TestCase):
    def test_equal_lines_have_distinct_stable_keys_and_file_scope(self):
        def records():
            return [{"source_row": n, "accounting_source_key": "same-values"} for n in (2, 3)]
        with patch.object(frappe, "get_all", return_value=[]):
            first = identify_lines(records(), "file-a")
            self.assertNotEqual(first[0]["accounting_source_key"], first[1]["accounting_source_key"])
            self.assertEqual(first, identify_lines(records(), "file-a"))
            self.assertNotEqual(first, identify_lines(records(), "file-b"))

    def test_existing_evidence_reused_without_merging_second_physical_line(self):
        with patch.object(frappe, "get_all", side_effect=[[
            frappe._dict(source_row=2, accounting_source_key="old-key")], []]):
            rows = identify_lines([{"source_row": n, "accounting_source_key": "old-key"} for n in (2, 3)], "file-a")
        self.assertEqual(rows[0]["accounting_source_key"], "old-key")
        self.assertNotEqual(rows[1]["accounting_source_key"], "old-key")

    def test_similar_applications_stay_effective_in_same_and_different_imports(self):
        rows = [frappe._dict(event_type="Aplicacion", effective=1, match_status="Pendiente",
            loan_number="123-1", amount=100, currency="USD", voucher="V", reference="R",
            event_date="2025-04-15", _source_import=parent, idx=index)
            for index, parent in enumerate(("I1", "I1", "I2"), 1)]
        accounting._deduplicate_applications(rows)
        self.assertTrue(all(row.effective == 1 and row.match_status != "Ignorado" for row in rows))
