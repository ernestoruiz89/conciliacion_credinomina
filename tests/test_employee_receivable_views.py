"""The same confirmed employee shortfall must appear in every operational view."""

import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import control_credinomina
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo
from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion import resumen_de_conciliacion


class EmployeeReceivableViewsTest(unittest.TestCase):
    def setUp(self):
        self.period = frappe._dict({
            "name": "PER-001", "employer": "Empresa Uno", "payroll_month": "2026-09-01",
            "reconciliation_mode": "Operativa", "collection_cycle": "Mensual",
            "expected_usd": 200, "expected_nio": 7400,
            "deducted_usd": 70, "deducted_nio": 2590,
            "applied_usd": 70, "remitted_usd": 0,
            "rounding_adjustment_usd": 0, "fx_variance_usd": 0,
            "exception_count": 0, "status": "Abierto",
        })
        self.rows = [
            frappe._dict({
                "parent": "PER-001", "client_number": "1", "client_name": "Ana",
                "loan_number": "CR-1", "expected_usd": 100, "expected_nio": 3700,
                "deducted_usd": 70, "deducted_nio": 2590,
                "deduction_status": "Deduccion parcial",
            }),
            frappe._dict({
                "parent": "PER-001", "client_number": "2", "client_name": "Luis",
                "loan_number": "CR-2", "expected_usd": 100, "expected_nio": 3700,
                "deducted_usd": 0, "deducted_nio": 0,
                "deduction_status": "Pendiente de detalle",
            }),
        ]

    def get_all(self, doctype, **kwargs):
        if doctype == "CN Employer":
            return [frappe._dict({"name": "Empresa Uno", "employer_name": "Empresa Uno"})]
        if doctype == "CN Collection Row":
            return self.rows
        return []

    def test_dashboard_separates_employee_receivable_from_missing_detail(self):
        with patch.object(control_credinomina.frappe, "has_permission", side_effect=lambda doctype, *_: doctype == "CN Reconciliation Period"), \
             patch.object(control_credinomina.frappe, "get_list", return_value=[self.period]), \
             patch.object(control_credinomina.frappe, "get_all", side_effect=self.get_all):
            data = control_credinomina.get_control_data(year=2026)
        self.assertEqual(data["totals"]["worker_gap_usd"], 30)
        self.assertEqual(data["totals"]["pending_detail_usd"], 100)
        self.assertEqual(data["periods"][0]["rows"][0]["employee_receivable_usd"], 30)
        self.assertIsNone(data["periods"][0]["rows"][1]["employee_receivable_usd"])

    def test_reports_do_not_infer_worker_debt_from_missing_detail(self):
        with patch.object(resumen_de_conciliacion.frappe, "get_all", side_effect=lambda doctype, **kwargs: [self.period] if doctype == "CN Reconciliation Period" else self.get_all(doctype, **kwargs)):
            _columns, summary = resumen_de_conciliacion.execute()
        self.assertEqual(summary[0]["employee_shortfall_usd"], 30)
        self.assertEqual(summary[0]["employee_shortfall_nio"], 1110)

        with patch.object(estado_de_cuenta_operativo, "get_periods", return_value=[self.period]), \
             patch.object(estado_de_cuenta_operativo.frappe, "get_all", return_value=self.rows):
            _columns, detail = estado_de_cuenta_operativo.execute()
        self.assertEqual(detail[0]["employee_pending_usd"], 30)
        self.assertEqual(detail[0]["employee_pending_nio"], 1110)
        self.assertIsNone(detail[1]["employee_pending_usd"])
        self.assertIsNone(detail[1]["employee_pending_nio"])


if __name__ == "__main__":
    unittest.main()
