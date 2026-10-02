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
