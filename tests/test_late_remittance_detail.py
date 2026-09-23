"""A registered deposit may precede its per-client allocation evidence."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from credinomina_reconciliation.allocation import allocate_cash
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
    _prepare_remittance_details,
)


class LateRemittanceDetailTest(unittest.TestCase):
    def _plan(self, instructions=()):
        deposit = SimpleNamespace(
            name="D-1", deposit_date="2026-10-12", employer="Empresa A",
            detail_file=None, detail_hash=None,
        )
        with patch(
            "credinomina_reconciliation.conciliacion_credinomina.doctype."
            "cn_source_import.cn_source_import.frappe.get_all",
            return_value=[],
        ):
            return _prepare_remittance_details(
                [deposit], {"D-1": "R:D-1"},
                [{"id": "R:D-1", "reference": "REF", "amount_usd": 100}],
                [{"id": "C:1", "references": ["REF"], "group": "Empresa A"}],
                list(instructions), {},
            )

    def test_single_matching_reference_stays_pending_without_detail(self):
        plan = self._plan()
        self.assertEqual({"R:D-1"}, plan["blocked_deposits"])
        self.assertEqual("Detalle pendiente", plan["contexts"]["D-1"]["status"])
        self.assertFalse(plan["instructions"])
        result = allocate_cash(
            [{"id": "R:D-1", "reference": "REF", "amount_usd": 100}],
            [{"id": "C:1", "references": ["REF"], "amount_usd": 100}],
            blocked_deposit_ids=plan["blocked_deposits"],
        )
        self.assertFalse(result["allocations"])
        self.assertEqual(100, result["deposit_remaining"]["R:D-1"])

    def test_explicit_full_distribution_can_replace_late_file(self):
        instruction = {
            "id": "manual-1", "deposit_id": "R:D-1",
            "claim_id": "C:1", "amount_usd": 100,
        }
        plan = self._plan([instruction])
        self.assertEqual({"R:D-1"}, plan["blocked_deposits"])
        self.assertEqual("Distribución manual", plan["contexts"]["D-1"]["status"])
        result = allocate_cash(
            [{"id": "R:D-1", "reference": "REF", "amount_usd": 100}],
            [{"id": "C:1", "references": ["REF"], "amount_usd": 100}],
            instructions=[instruction],
            blocked_deposit_ids=plan["blocked_deposits"],
        )
        self.assertEqual("Aplicada", result["instruction_results"]["manual-1"])
        self.assertEqual(0, result["deposit_remaining"]["R:D-1"])


if __name__ == "__main__":
    unittest.main()
