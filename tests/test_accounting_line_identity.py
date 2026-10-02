import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_identity import identify_lines
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as accounting


def evidence(**changes):
    return frappe._dict(source_row=2, event_type="Ajuste", event_date="2025-04-01",
        source_date="2025-04-01", source_account="1602", source_currency="NIO",
        source_debit=100, source_credit=0, source_voucher="V", voucher="V",
        source_description="Evidencia original", accounting_source_key="old-key", **changes)


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
            evidence()], []]):
            rows = identify_lines([dict(evidence(), source_row=n) for n in (2, 3)], "file-a")
        self.assertEqual(rows[0]["accounting_source_key"], "old-key")
        self.assertNotEqual(rows[1]["accounting_source_key"], "old-key")

    def test_reject_shared_position_with_unrelated_deposit_or_complement(self):
        for doctype in ("CN Complementary Item", "CN Remittance Allocation"):
            for difference in ({"source_date": "2025-04-02", "event_date": "2025-04-02"},
                               {"source_voucher": "OTHER", "voucher": "OTHER"},
                               {"source_debit": 101}, {"source_description": "Otro movimiento"},
                               {"source_account": "OTRA"}, {"source_currency": "USD"}):
                old = frappe._dict({**evidence(), **difference})
                with self.subTest(doctype=doctype, difference=difference), patch.object(frappe, "get_all",
                        side_effect=lambda table, **kw: [old] if table == doctype else []):
                    row = dict(evidence(), event_type="Deposito" if doctype == "CN Remittance Allocation" else "Ajuste")
                    self.assertNotEqual(identify_lines([row], "file-a")[0]["accounting_source_key"], "old-key")

    def test_application_never_reuses_deposit_or_complement_identity(self):
        with patch.object(frappe, "get_all", return_value=[evidence()]):
            row = dict(evidence(), event_type="Aplicacion")
            self.assertNotEqual(identify_lines([row], "file-a")[0]["accounting_source_key"], "old-key")

    def test_real_deposit_identity_stays_stable(self):
        old = evidence()
        with patch.object(frappe, "get_all", side_effect=[[], [old]]):
            row = dict(evidence(), event_type="Deposito", accounting_classification="Depósito")
            self.assertEqual(identify_lines([row], "file-a")[0]["accounting_source_key"], "old-key")

    def test_similar_applications_stay_effective_in_same_and_different_imports(self):
        rows = [frappe._dict(event_type="Aplicacion", effective=1, match_status="Pendiente",
            loan_number="123-1", amount=100, currency="USD", voucher="V", reference="R",
            event_date="2025-04-15", _source_import=parent, idx=index)
            for index, parent in enumerate(("I1", "I1", "I2"), 1)]
        accounting._deduplicate_applications(rows)
        self.assertTrue(all(row.effective == 1 and row.match_status != "Ignorado" for row in rows))
