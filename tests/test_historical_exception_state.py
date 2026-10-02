"""A matched historical cash flow is not cleared while an exception is open."""

import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import (
    control_credinomina,
)


class HistoricalExceptionStateTests(unittest.TestCase):
    def test_open_exception_overrides_historical_reconciled_badge(self):
        period = frappe._dict({
            "name": "H-2025-04-15", "employer": "EMP-1",
            "payroll_month": "2025-04-01", "reconciliation_mode": "Historica",
            "status": "Conciliado", "applied_usd": 100,
            "remitted_usd": 100, "rounding_adjustment_usd": 0,
            "fx_variance_usd": 0, "exception_count": 1,
        })

        def get_all(doctype, **_kwargs):
            if doctype == "CN Employer":
                return [frappe._dict(name="EMP-1", employer_name="Empresa Uno")]
            return []

        def get_list(doctype, **_kwargs):
            if doctype == "CN Reconciliation Period":
                return [period]
            if doctype == "CN Reconciliation Exception":
                return [frappe._dict(
                    name="EX-1", period=period.name, status="Abierta",
                    exception_type="Pago sin identificar", amount_usd=10,
                )]
            return []

        with patch.object(control_credinomina.frappe, "has_permission",
                          side_effect=lambda doctype, *_args: doctype in {
                              "CN Reconciliation Period", "CN Reconciliation Exception",
                          }), \
             patch.object(control_credinomina.frappe, "get_list", side_effect=get_list), \
             patch.object(control_credinomina.frappe, "get_all", side_effect=get_all), \
             patch.object(control_credinomina, "collection_summaries", return_value={}):
            data = control_credinomina.get_control_data(year=2025)
            detail = control_credinomina._build_control_data("Todos", detail_period=period.name)["periods"][0]

        self.assertEqual(data["periods"][0]["control_state"], "historico_excepcion")
        self.assertNotIn("exceptions", data["periods"][0])
        self.assertEqual(len(detail["exceptions"]), 1)


if __name__ == "__main__":
    unittest.main()
