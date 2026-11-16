import copy
import json
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as remittance
from credinomina_reconciliation.remittance_detail import suggest_detail_targets
from credinomina_reconciliation.parsers import normalize_returned_credit_number


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    _next_child = 0

    def set(self, field, value):
        self[field] = value

    def append(self, field, value):
        row = Record(value)
        if not row.get("name"):
            # save() is mocked; give new children the stable names it would assign.
            Record._next_child += 1
            row.name = f"TEST-ROW-{Record._next_child}"
        self.setdefault(field, []).append(row)
        return row  # Frappe returns the child so callers can set its idx.


class RemittanceCreditNormalizationTests(unittest.TestCase):
    def setUp(self):
        # Supply repository reads; keep identity and preservation rules real.
        self.enterContext(patch.object(remittance, "load_client_index", return_value=[
            {"name": name, "client_number": name, "client_name": name, "employer": "A"}
            for name in ("Ana", "Beatriz", "Carlos", "Diana")]))
        self.enterContext(patch.object(remittance, "load_detail_loan_clients", return_value={}))
        self.enterContext(patch.object(remittance, "allowed_employers", return_value={"A"}))
        self.enterContext(patch("credinomina_reconciliation.paying_employers.load_payer_map", return_value={}))
        self.enterContext(patch("credinomina_reconciliation.client_credit.load_credits", return_value=[]))

    def test_returned_credit_keeps_original_digits_without_padding(self):
        for raw, expected in ((1807, "1807-1"), (11, "11-1"), (1122, "1122-1"),
                              (12230, "12230-1"), (1807.0, "1807-1"),
                              ("1807-1", "1807-1"), ("107997-1", "107997-1"),
                              ("001807", "001807-1"), ("1234567", "1234567-1"),
                              ("1807-2", "1807-2"), ("", ""), (None, ""), ("ABC", "ABC")):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_returned_credit_number(raw), expected)

    def test_file_import_normalizes_numeric_credits_and_preserves_existing_suffixes(self):
        content = (
            "Nombre y Apellidos del Cliente,Nro. Crédito,Deducido US$\n"
            "Ana,13375,50\n"
            "Beatriz,13376-1,50\n"
            "Carlos,13377-2,50\n"
            "Diana,'0013378,50\n"
            "Elena,,50\n"
            "Felipe,L-13379,50\n"
        ).encode("utf-8")
        document = Record(
            name="DEP-4-2025-0001", doctype="CN Remittance Allocation", employer="A",
            deposit_date="2025-04-30", docstatus=0, detail_file="/private/files/detail.csv",
            targets=[], check_permission=Mock(), save=Mock(),
        )
        file_doc = Record(file_name="detail.csv", get_content=lambda: content)
        with (
            patch.object(remittance.frappe, "get_doc", side_effect=lambda dt, name: document if dt == document.doctype else file_doc),
            patch.object(remittance.frappe, "get_all", return_value=["FILE"]),
            patch.object(remittance, "load_client_index", return_value=[]),
            patch.object(remittance, "allowed_employers", return_value={"A"}),
            patch.object(remittance, "now_datetime", return_value="2026-10-02 10:00:00"),
        ):
            result = remittance.import_remittance_detail(document.name)
            self.assertEqual([row.loan_number for row in document.detail_rows],
                             ["13375-1", "13376-1", "13377-2", "0013378-1", "", "L-13379"])
            self.assertEqual(result["rows"], 6)
            self.assertEqual([row.amount_usd for row in document.detail_rows], [50] * 6)
            self.assertEqual([row.pending_usd for row in document.detail_rows], [50] * 6)
            self.assertEqual(document.detail_total_usd, 300)
            self.assertEqual(document.detail_status, "Cargado; pendiente de conciliación")
            document.save.assert_called_once()
            # Re-importing the raw file must not accumulate prefixes or suffixes.
            remittance.import_remittance_detail(document.name)
            self.assertEqual(document.detail_rows[0].loan_number, "13375-1")

        targets, _ = suggest_detail_targets(
            document.detail_rows[0], [{"id": "H:APP", "kind": "H", "group": "A",
                "loan_number": "0013375-1", "client_name": "Ana", "amount_usd": 50}], 50, "A",
        )
        self.assertEqual(targets, [{"claim_id": "H:APP", "amount_usd": 50}])

    def test_import_calculates_equivalents_before_save_without_allocating_cash(self):
        for status in (0, 1):
            with self.subTest(docstatus=status):
                document = Record(name="DEP", doctype="CN Remittance Allocation", docstatus=status,
                    employer="A", fx_rate="36.6243", targets=[], save=Mock(),
                    allocated_usd=0, unallocated_usd=71.73, allocation_detail="[]")
                records = [
                    {"client_name": "Ana", "deducted_usd": "2.675"},
                    {"client_name": "Beatriz", "deducted_nio": "824.78"},
                    {"client_name": "Carlos", "deducted_usd": "46.53", "deducted_nio": "1700"},
                    {"client_name": "Diana", "deducted_usd": 0, "deducted_nio": 0},
                ]
                with patch.object(remittance, "now_datetime", return_value="2026-10-04 10:00:00"):
                    remittance._apply_remittance_detail(document, records, b"file", "/private/files/detail.xlsx")
                    self.assertEqual([row.amount_usd for row in document.detail_rows], [2.68, 22.52, 46.53, 0])
                    self.assertEqual(document.detail_total_usd, 71.73)
                    self.assertEqual([row.pending_usd for row in document.detail_rows], [2.68, 22.52, 46.53, 0])
                    self.assertEqual([row.match_status for row in document.detail_rows], ["Pendiente"] * 3 + ["No deducido"])
                    self.assertEqual(document.allocated_usd, 0)
                    self.assertEqual(document.unallocated_usd, 71.73)
                    self.assertEqual(document.allocation_detail, "[]")
                    self.assertEqual(document.targets, [])
                    document.save.assert_called_once()
                    # Unlinked, unpaid rows may still be replaced; amounts do not accumulate.
                    remittance._apply_remittance_detail(document, records[:1], b"new file", "/private/files/detail.xlsx")
                    self.assertEqual(document.detail_total_usd, 2.68)
                    self.assertEqual(len(document.detail_rows), 1)

    def linked_detail(self, status=1):
        records = [dict(client_name="Ana", deducted_usd=2.68),
                   dict(client_name="Beatriz", deducted_nio=824.78)]
        rows = [Record(record, name=f"ROW-{record['client_name']}", employer="A",
            client=record["client_name"], client_number=record["client_name"], amount_usd=amount,
            linked_usd=0, pending_usd=amount, match_status="Pendiente", matched_targets="[]",
            difference_reason="Manual", difference_notes="Conservar documentación")
            for record, amount in zip(records, (2.68, 22.52))]
        targets = [Record(name="T1", detail_row="ROW-Beatriz", historical_application="APP1", amount_usd=10),
            Record(name="T2", detail_row="ROW-Beatriz", historical_application="APP2", amount_usd=12.52),
            Record(name="T3", detail_row="ROW-Ana", complementary_item="CORE", amount_usd=2.68)]
        document = Record(name="DEP", doctype="CN Remittance Allocation", docstatus=status, employer="A",
            fx_rate=36.6243, detail_rows=rows, targets=targets, save=Mock(),
            detail_hash=remittance.file_sha256(b"old"), detail_source_file="detail.xlsx",
            detail_total_usd=25.20, allocated_usd=0, unallocated_usd=25.20, allocation_detail="[]")
        return document, records

    @staticmethod
    def stored(document):
        return copy.deepcopy({key: value for key, value in document.items() if not callable(value)})

    def test_reimport_reorders_detail_preserving_all_targets_and_documentation(self):
        for status in (0, 1):
            with self.subTest(docstatus=status):
                document, records = self.linked_detail(status)
                before = self.stored(document)
                remittance._apply_remittance_detail(document, list(reversed(records)), b"reordered", "detail.xlsx")
                self.assertEqual([row.name for row in document.detail_rows], ["ROW-Beatriz", "ROW-Ana"])
                self.assertEqual([row.idx for row in document.detail_rows], [1, 2])
                self.assertEqual(document.targets, before["targets"])
                self.assertEqual(document.detail_total_usd, 25.20)
                for field in ("allocated_usd", "unallocated_usd", "allocation_detail"):
                    self.assertEqual(document[field], before[field])
                by_name = {row.name: row for row in document.detail_rows}
                for old in before["detail_rows"]:
                    for field, value in old.items():
                        self.assertEqual(by_name[old.name][field], value)
                document.save.assert_called_once()

    def test_reimport_rejects_missing_changed_and_ambiguous_linked_rows_without_mutating(self):
        for status in (0, 1):
            for change in ("missing", "amount", "duplicate"):
                with self.subTest(docstatus=status, change=change):
                    document, records = self.linked_detail(status)
                    if change == "missing":
                        records.pop()
                    elif change == "amount":
                        records[1]["deducted_nio"] = 824.79
                    else:
                        records.append(dict(records[1]))
                    before = self.stored(document)
                    with self.assertRaisesRegex(frappe.ValidationError, "fila vinculada"):
                        remittance._apply_remittance_detail(document, records, b"changed", "detail.xlsx")
                    self.assertEqual(self.stored(document), before)
                    document.save.assert_not_called()

    def test_identical_file_preserves_confirmed_cash_and_different_file_is_rejected(self):
        document, records = self.linked_detail()
        document.allocated_usd, document.unallocated_usd = 22.52, 2.68
        document.allocation_detail = json.dumps([
            dict(tipo="Aplicacion historica", aplicacion_id=target.historical_application,
                 fila_detalle=target.detail_row, importe_usd=target.amount_usd)
            for target in document.targets if target.historical_application])
        row = document.detail_rows[1]
        row.linked_usd, row.pending_usd, row.match_status = 22.52, 0, "Conciliada"
        row.matched_targets = json.dumps([dict(claim_id="H:" + target.historical_application,
            amount_usd=target.amount_usd) for target in document.targets if target.historical_application])
        before = self.stored(document)
        remittance._apply_remittance_detail(document, records, b"old", "detail.xlsx")
        self.assertEqual(self.stored(document), before)
        document.save.assert_not_called()
        with self.assertRaisesRegex(frappe.ValidationError, "filas conciliadas"):
            remittance._apply_remittance_detail(document, list(reversed(records)), b"different", "detail.xlsx")
        self.assertEqual(self.stored(document), before)
        document.save.assert_not_called()

    def test_import_without_rate_keeps_nio_row_for_review_instead_of_treating_it_as_usd(self):
        document = Record(name="DEP", employer="A", docstatus=0, targets=[], save=Mock())
        with patch.object(remittance, "load_client_index", return_value=[{"name": "A1", "client_name": "Ana", "employer": "A"}]), \
                patch.object(remittance, "allowed_employers", return_value={"A"}), \
                patch.object(remittance, "now_datetime", return_value="2026-10-04 10:00:00"):
            remittance._apply_remittance_detail(document, [{"client_name": "Ana", "deducted_nio": 824.78}], b"file", "detail.xlsx")
        self.assertEqual(document.detail_rows[0].amount_usd, 0)
        self.assertEqual(document.detail_rows[0].match_status, "Revisar")
        self.assertIn("Falta tasa", document.detail_rows[0].match_reason)
        self.assertEqual(document.detail_total_usd, 0)
