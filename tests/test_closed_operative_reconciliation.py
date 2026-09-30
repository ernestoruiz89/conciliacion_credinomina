"""A closed operative period cannot be silently rewritten by source reconciliation."""

import json
import unittest
from collections import defaultdict
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import import (
    cn_source_import as source_module,
)


def _closed_period(**row_values):
    row = frappe._dict({
        "name": "COL-1", "parent": "PER-1", "row_key": "ROW-1",
        "expected_usd": 0, "expected_nio": 0, "deducted_usd": 0,
        "deducted_nio": 0, "applied_usd": 0, "applied_nio": 0,
        "complementary_usd": 0, "remitted_usd": 0, "remitted_nio": 0,
        "fx_variance_usd": 0, "rounding_adjustment_usd": 0,
        "deduction_status": "Pendiente de detalle", "deduction_currency": "",
        "application_status": "Pendiente", "remittance_detail": "[]",
        "inherited_exception_comment": "",
    })
    row.update(row_values)
    period = frappe._dict({
        "name": "PER-1", "status": "Cerrado", "exception_count": 0,
        "deduction_basis": "", "deduction_recognition_deposit": "",
        "deduction_recognition_reference": "", "collection_rows": [row],
        "recalculate_totals": Mock(), "save": Mock(),
    })
    return period, row


def _allocation(registered=None):
    registered = registered or {}
    return {
        "complementary_totals": defaultdict(float),
        "allocations": [], "rounding_movements": [],
        "deposit_meta": registered,
        "registered_ids": {key: key for key in registered},
    }


class ClosedOperativeReconciliationTests(unittest.TestCase):
    def _rebuild(self, period, allocation=None, old_links=None):
        state = {period.name: source_module._operative_period_state(period)}
        links = old_links or source_module._operative_links(
            [period], [], [], [], state,
        )
        with patch.object(source_module, "_transfer_matching_exception_notes"):
            source_module._rebuild_period_balances(
                [period], [], [], allocation or _allocation(), state, links, [],
            )

    def test_idempotent_recalculation_does_not_save_closed_period(self):
        period, _row = _closed_period()

        self._rebuild(period)

        period.save.assert_not_called()

    def test_changed_balance_status_or_detail_requires_reopening(self):
        for changed in (
            {"applied_usd": 10},
            {"application_status": "Aplicado y remitido"},
            {"remittance_detail": json.dumps([{"referencia": "DEP-1", "importe_usd": 10}])},
        ):
            with self.subTest(changed=changed):
                period, _row = _closed_period(**changed)
                with patch.object(source_module.frappe, "throw", side_effect=ValueError) as reject:
                    with self.assertRaises(ValueError):
                        self._rebuild(period)
                self.assertIn("Reabrir período", reject.call_args.args[0])
                period.save.assert_not_called()

    def test_replaced_deposit_is_detected_even_if_amount_stays_equal(self):
        period, _row = _closed_period()
        state = {period.name: source_module._operative_period_state(period)}
        cash_link = json.dumps([{
            "tipo": "Cobranza", "periodo": period.name,
            "fila_id": "ROW-1", "credito": "LOAN-1", "importe_usd": 10,
        }])
        previous = source_module._operative_links(
            [period], [frappe._dict({
                "name": "DEP-OLD", "event_type": "Deposito",
                "allocation_detail": cash_link,
            })], [], [], state,
        )
        replacement = source_module._operative_links(
            [period], [frappe._dict({
                "name": "DEP-NEW", "event_type": "Deposito",
                "allocation_detail": cash_link,
            })], [], [], state,
        )

        self.assertNotEqual(previous, replacement)
        with patch.object(source_module.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                self._rebuild(
                    period,
                    _allocation({"DEP-NEW": {"account": frappe._dict({
                        "name": "DEP-NEW", "allocation_detail": cash_link,
                    })}}),
                    previous,
                )
        period.save.assert_not_called()

    def test_provisional_application_counts_as_applied_but_not_remitted(self):
        period, row = _closed_period(expected_usd=50)
        period.status = "Pendiente"
        application = frappe._dict({
            "name": "APP-1", "event_type": "Aplicacion", "effective": 1,
            "match_status": source_module.PROVISIONAL_APPLICATION,
            "application_allocation_detail": json.dumps([{
                "collection_row_id": row.name, "amount_usd": 50,
            }]),
            "currency": "USD", "reference": "R-1",
        })

        with patch.object(source_module, "_transfer_matching_exception_notes"):
            source_module._rebuild_period_balances(
                [period], [application], [], _allocation(), {}, {}, [],
            )

        self.assertEqual(row.applied_usd, 50)
        self.assertEqual(row.application_status, "Aplicacion encontrada")
        self.assertEqual(application.deposit_match_status, "Pendiente")
        self.assertIn("provisionalmente", application.deposit_match_reason)
        period.save.assert_called_once()

    def test_paid_row_does_not_clear_undeducted_or_pending_rows(self):
        period, paid = _closed_period(
            deduction_status="Deduccion total",
            application_status="Aplicado y remitido",
        )
        period.status = "Conciliado"
        self.assertTrue(source_module._operative_period_fully_reconciled(period))

        for incomplete_status in ("No deducido", "Pendiente de detalle", "Deduccion parcial"):
            with self.subTest(incomplete_status=incomplete_status):
                other = frappe._dict({
                    **paid, "name": "COL-2", "row_key": "ROW-2",
                    "deduction_status": incomplete_status,
                    "application_status": "Pendiente",
                })
                period.collection_rows = [paid, other]
                self.assertFalse(source_module._operative_period_fully_reconciled(period))
        period.collection_rows = [paid]
        period.exception_count = 1
        self.assertFalse(source_module._operative_period_fully_reconciled(period))


if __name__ == "__main__":
    unittest.main()
