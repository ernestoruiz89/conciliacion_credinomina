"""A previously resolved deduction case must reopen after it disappears and recurs."""

import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import (
    cn_reconciliation_period as period_module,
)


class _Exception:
    def __init__(self):
        self.name = "EX-1"
        self.status = "Resuelta"
        self.exception_type = "Deduccion parcial"
        self.exception_key = "DED-stable"
        self.amount_usd = 10
        self.amount_nio = 370
        self.resolution = "Empresa confirmó subsidio; se cobrará al retorno"
        self.follow_up_actions = []
        self.flags = frappe._dict()

    def append(self, _field, value):
        self.follow_up_actions.append(value)

    def update(self, values):
        for key, value in values.items():
            setattr(self, key, value)

    def save(self, **_kwargs):
        return self


class _Database:
    def get_value(self, _doctype, filters, _field):
        return "EX-1" if filters.get("exception_key") == "DED-stable" else None


class DeductionExceptionLifecycleTest(unittest.TestCase):
    def test_resolved_disappears_then_same_difference_reopens_with_audit(self):
        exception = _Exception()
        period = frappe._dict(name="P-1", employer="EMP")

        def get_all(_doctype, *, filters, **_kwargs):
            if (filters.get("exception_key") == ["like", "DED-%"]
                    and exception.status in filters["status"][1]):
                return [exception.name]
            return []

        values = dict(
            exception_type="Deduccion parcial", exception_key="DED-stable",
            amount_usd=10, amount_nio=370, collection_row_id="ROW-1",
        )
        with patch.object(period_module.frappe, "db", _Database()), \
             patch.object(period_module.frappe, "get_all", side_effect=get_all), \
             patch.object(period_module.frappe, "get_doc", return_value=exception):
            # Reimporting an unchanged, continuously present difference does
            # not erase an existing human resolution.
            self.assertEqual(period_module._create_exception(period, **values), "EX-1")
            self.assertEqual(exception.status, "Resuelta")
            self.assertEqual(exception.follow_up_actions, [])

            period_module._retire_obsolete_deduction_exceptions(period.name, set())
            self.assertEqual(exception.status, "Descartada")
            self.assertIn("Empresa confirmó subsidio", exception.follow_up_actions[0]["details"])
            self.assertEqual(len(exception.follow_up_actions), 1)

            # Another clean import is idempotent.
            period_module._retire_obsolete_deduction_exceptions(period.name, set())
            self.assertEqual(len(exception.follow_up_actions), 1)

            self.assertEqual(period_module._create_exception(period, **values), "EX-1")
            self.assertEqual(exception.status, "Abierta")
            self.assertEqual(exception.resolution, "")
            self.assertEqual(len(exception.follow_up_actions), 2)
            self.assertIn("se reabre", exception.follow_up_actions[1]["details"])


if __name__ == "__main__":
    unittest.main()
