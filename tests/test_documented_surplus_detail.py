"""A documented company credit clears only the unexplained detail remainder."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import import (
    cn_source_import as source_module,
)


class DocumentedSurplusDetailTests(unittest.TestCase):
    def _sync(
        self, justified, unclassified, detail_status="Parcial; saldo sin detalle",
        result="Parcial", detail_file="", detail_hash="",
    ):
        source = frappe._dict({
            "name": "REM-1", "allocated_usd": 100,
            "unallocated_usd": 10, "justified_surplus_usd": justified,
            "unclassified_usd": unclassified, "allocation_detail": "[]",
            "inherited_exception_comment": "",
        })
        allocation = {
            "registered_ids": {"REM-1": "REM-1"},
            "deposit_meta": {"REM-1": {"account": source}},
        }

        def get_value(_doctype, _name, fieldname):
            return {
                "result": result, "detail_status": detail_status,
                "detail_file": detail_file, "detail_hash": detail_hash,
            }[fieldname]

        save = Mock()
        with patch.object(
            source_module.frappe, "db",
            SimpleNamespace(get_value=get_value, set_value=save),
        ):
            source_module._sync_registered_deposit_detail(allocation)
        return save.call_args.args[2]

    def test_whole_remainder_documented_clears_detail_only(self):
        update = self._sync(justified=10, unclassified=0)
        self.assertEqual(update["detail_status"], "Conciliado; excedente documentado")
        self.assertEqual(update["result"], "Parcial con saldo a favor")
        self.assertEqual(update["justified_surplus_usd"], 10)
        self.assertEqual(update["unclassified_usd"], 0)

    def test_partial_or_removed_justification_returns_to_review(self):
        for justified, unclassified in ((5, 5), (0, 10)):
            with self.subTest(justified=justified):
                update = self._sync(justified=justified, unclassified=unclassified)
                self.assertEqual(update["detail_status"], "Parcial; saldo sin detalle")
                self.assertEqual(update["result"], "Parcial")

    def test_invalid_detail_is_not_cleared_by_surplus(self):
        update = self._sync(10, 0, detail_status="Revisar filas")
        self.assertEqual(update["detail_status"], "Revisar filas")

    def test_manual_split_plus_documented_surplus_needs_no_file(self):
        update = self._sync(
            10, 0, detail_status="Detalle pendiente", result="Detalle pendiente",
        )
        self.assertEqual(update["detail_status"], "Distribución manual; excedente documentado")
        self.assertEqual(update["result"], "Parcial con saldo a favor")

    def test_invalid_destinations_or_attached_file_stay_pending(self):
        for result, detail_file in (
            ("Revisar destinos", ""),
            ("Detalle pendiente", "/private/files/detalle.xlsx"),
        ):
            with self.subTest(result=result, detail_file=detail_file):
                update = self._sync(
                    10, 0, detail_status="Detalle pendiente",
                    result=result, detail_file=detail_file,
                )
                self.assertEqual(update["detail_status"], "Detalle pendiente")
                self.assertEqual(update["result"], result)

    def test_manual_split_reopens_when_surplus_is_removed(self):
        update = self._sync(
            0, 10, detail_status="Detalle pendiente", result="Detalle pendiente",
        )
        self.assertEqual(update["detail_status"], "Detalle pendiente")
        self.assertEqual(update["result"], "Detalle pendiente")


if __name__ == "__main__":
    unittest.main()
