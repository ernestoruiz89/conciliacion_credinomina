import unittest
from unittest.mock import Mock, patch

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as remittance
from credinomina_reconciliation.remittance_detail import suggest_detail_targets


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def set(self, field, value):
        self[field] = value

    def append(self, field, value):
        self.setdefault(field, []).append(Record(value))


class RemittanceCreditNormalizationTests(unittest.TestCase):
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
            # Re-importing the same file must not append the suffix twice.
            remittance.import_remittance_detail(document.name)
            self.assertEqual(document.detail_rows[0].loan_number, "13375-1")

        targets, _ = suggest_detail_targets(
            document.detail_rows[0], [{"id": "H:APP", "kind": "H", "group": "A",
                "loan_number": "13375-1", "client_name": "Ana", "amount_usd": 50}], 50, "A",
        )
        self.assertEqual(targets, [{"claim_id": "H:APP", "amount_usd": 50}])

    def test_import_calculates_equivalents_before_save_without_allocating_cash(self):
        for status in (0, 1):
            with self.subTest(docstatus=status):
                target = Record(detail_row="OLD", detail_row_label="Old row", amount_usd=22.52)
                document = Record(name="DEP", doctype="CN Remittance Allocation", docstatus=status,
                    employer="A", fx_rate="36.6243", targets=[target], save=Mock(),
                    allocated_usd=22.52, unallocated_usd=100, allocation_detail="existing distribution")
                records = [
                    {"client_name": "Ana", "deducted_usd": "2.675"},
                    {"client_name": "Beatriz", "deducted_nio": "824.78"},
                    {"client_name": "Carlos", "deducted_usd": "46.53", "deducted_nio": "1700"},
                    {"client_name": "Diana", "deducted_usd": 0, "deducted_nio": 0},
                ]
                with patch.object(remittance, "load_client_index", return_value=[
                        {"name": name, "client_name": name, "employer": "A"}
                        for name in ("Ana", "Beatriz", "Carlos", "Diana")]), \
                        patch.object(remittance, "allowed_employers", return_value={"A"}), \
                        patch("credinomina_reconciliation.client_credit.guard_detail_replacement"), \
                        patch.object(remittance, "now_datetime", return_value="2026-10-04 10:00:00"):
                    remittance._apply_remittance_detail(document, records, b"file", "/private/files/detail.xlsx")
                    self.assertEqual([row.amount_usd for row in document.detail_rows], [2.68, 22.52, 46.53, 0])
                    self.assertEqual(document.detail_total_usd, 71.73)
                    self.assertEqual([row.pending_usd for row in document.detail_rows], [2.68, 22.52, 46.53, 0])
                    self.assertEqual([row.match_status for row in document.detail_rows], ["Pendiente"] * 3 + ["No deducido"])
                    self.assertEqual(document.allocated_usd, 22.52)
                    self.assertEqual(document.allocation_detail, "existing distribution")
                    self.assertEqual(target.amount_usd, 22.52)
                    self.assertEqual(target.detail_row, "")
                    document.save.assert_called_once()
                    # Reimport replaces the total and rows rather than accumulating them.
                    remittance._apply_remittance_detail(document, records[:1], b"new file", "/private/files/detail.xlsx")
                    self.assertEqual(document.detail_total_usd, 2.68)
                    self.assertEqual(len(document.detail_rows), 1)

    def test_import_without_rate_keeps_nio_row_for_review_instead_of_treating_it_as_usd(self):
        document = Record(name="DEP", employer="A", targets=[], save=Mock())
        with patch.object(remittance, "load_client_index", return_value=[{"name": "A1", "client_name": "Ana", "employer": "A"}]), \
                patch.object(remittance, "allowed_employers", return_value={"A"}), \
                patch("credinomina_reconciliation.client_credit.guard_detail_replacement"), \
                patch.object(remittance, "now_datetime", return_value="2026-10-04 10:00:00"):
            remittance._apply_remittance_detail(document, [{"client_name": "Ana", "deducted_nio": 824.78}], b"file", "detail.xlsx")
        self.assertEqual(document.detail_rows[0].amount_usd, 0)
        self.assertEqual(document.detail_rows[0].match_status, "Revisar")
        self.assertIn("Falta tasa", document.detail_rows[0].match_reason)
        self.assertEqual(document.detail_total_usd, 0)
