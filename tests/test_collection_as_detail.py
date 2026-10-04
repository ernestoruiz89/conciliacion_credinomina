import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as module


class CollectionAsDetailTests(unittest.TestCase):
    def period(self):
        return SimpleNamespace(
            name="P1", employer="EMP", reconciliation_mode="Operativa", status="Pendiente",
            deduction_basis="", employer_response_file="", notes="Observación previa",
            collection_rows=[frappe._dict(expected_usd=20.125, expected_nio=740,
                deducted_usd=0, deducted_nio=0, deduction_status="Pendiente de detalle",
                applied_usd=10, remitted_usd=0)], flags=frappe._dict(),
            check_permission=Mock(), reload=Mock(), save=Mock(),
        )

    def call(self, period, **args):
        with patch.object(module.frappe, "get_doc", return_value=period), \
                patch.object(module.frappe, "db", SimpleNamespace(sql=Mock())), \
                patch.object(module.frappe, "session", SimpleNamespace(user="operador")), \
                patch.object(module, "now_datetime", return_value="2026-09-30 12:00:00"), \
                patch.object(module.frappe, "throw", side_effect=ValueError), \
                patch.object(module, "_reconcile_if_sources", return_value=None) as reconcile:
            result = module.recognize_collection_as_employer_detail("P1", **{
                "evidence_date": "2026-09-30", "confirmed": 1, **args})
            reconcile.assert_called_once_with("EMP")
            return result

    def test_copies_with_decimal_rounding_and_audit_without_deposit(self):
        period = self.period()
        result = self.call(period)
        row = period.collection_rows[0]
        self.assertEqual((row.deducted_usd, row.deducted_nio), (20.13, 740))
        self.assertEqual(row.deduction_currency, "Ambas")
        self.assertEqual(row.deduction_status, "Deduccion total")
        self.assertEqual(str(row.deduction_evidence_date), "2026-09-30")
        self.assertEqual(period.deduction_basis, "Detalle de empresa")
        self.assertEqual(period.status, "Pendiente")
        self.assertEqual((row.applied_usd, row.remitted_usd), (10, 0))
        self.assertIn("operador", row.deduction_match_note)
        self.assertIn("Observación previa", period.notes)
        self.assertEqual(result["rows"], 1)
        period.check_permission.assert_called_once_with("write")
        period.save.assert_called_once()

    def test_blocks_closed_history_and_existing_details(self):
        for changes in ({"status": "Cerrado"}, {"reconciliation_mode": "Historica"},
                        {"deduction_basis": "Detalle de empresa"}, {"deduction_basis": "Depósito coincidente"},
                        {"employer_response_file": "/private/files/detail.xlsx"}, {"collection_rows": []}):
            period = self.period()
            for field, value in changes.items():
                setattr(period, field, value)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.call(period)
            period.save.assert_not_called()
        for changes in ({"deducted_usd": 3}, {"deduction_status": "No deducido"},
                        {"expected_usd": -1}, {"expected_usd": 0, "expected_nio": 0}):
            period = self.period()
            period.collection_rows[0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.call(period)
            period.save.assert_not_called()

    def test_requires_confirmation_and_date_and_rejects_second_call(self):
        for args in ({"confirmed": 0}, {"evidence_date": ""}):
            period = self.period()
            with self.assertRaises(ValueError):
                self.call(period, **args)
            period.save.assert_not_called()
        period = self.period()
        self.call(period)
        with self.assertRaises(ValueError):
            self.call(period)
        period.save.assert_called_once()
