"""A documented surplus cannot change a deposit linked to a closed period."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from credinomina_reconciliation import company_credit as surplus_module


class ClosedDepositSurplusTests(unittest.TestCase):
    def _check(self, *, own_period=None, detail_period=None, target_period=None,
               allocated_period=None, status="Cerrado"):
        deposit = SimpleNamespace(
            detail_periods=[{"period": detail_period}] if detail_period else [],
            targets=[SimpleNamespace(
                period=target_period, historical_application=None,
                complementary_item=None,
            )] if target_period else [],
            allocation_detail=json.dumps(
                [{"periodo": allocated_period}] if allocated_period else []
            ),
        )
        surplus = SimpleNamespace(period=own_period, registered_deposit="REM-1")

        def get_value(doctype, name, field):
            if doctype == "CN Reconciliation Period" and field == "status":
                return status
            return None

        with (
            patch.object(surplus_module.frappe, "get_doc", return_value=deposit),
            patch.object(surplus_module.frappe, "db", SimpleNamespace(get_value=get_value)),
            patch.object(surplus_module.frappe, "throw", side_effect=ValueError) as rejected,
        ):
            if status == "Cerrado":
                with self.assertRaises(ValueError):
                    surplus_module.ensure_related_periods_open(surplus)
                self.assertEqual(rejected.call_count, 1)
            else:
                surplus_module.ensure_related_periods_open(surplus)
                rejected.assert_not_called()

    def test_explicit_closed_period_is_protected(self):
        self._check(own_period="PER-1")

    def test_deposit_target_closed_period_is_protected(self):
        self._check(target_period="PER-1")

    def test_deposit_detail_period_is_protected(self):
        self._check(detail_period="PER-1")

    def test_actual_allocation_closed_period_is_protected(self):
        self._check(allocated_period="PER-1")

    def test_open_related_period_can_receive_surplus(self):
        self._check(target_period="PER-1", status="Pendiente")


if __name__ == "__main__":
    unittest.main()
