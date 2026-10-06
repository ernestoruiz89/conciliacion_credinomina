import unittest

from credinomina_reconciliation.remittance_selection import pending_selection
from credinomina_reconciliation.allocation_origin import DETAIL, FIFO, MANUAL, REFERENCE


class PendingRemittanceSelectionTests(unittest.TestCase):
    def selection(self, rows, deposits=(), targets=(), amount=100):
        return pending_selection(rows, deposits, "CURRENT", amount, targets)

    def deposit(self, name, detail, status=1):
        return {"name": name, "docstatus": status, "allocation_detail": detail}

    def test_partial_historical_payment_and_cancelled_deposit(self):
        row = {"historical_application": "APP-APRIL", "due_usd": 100, "applied_usd": 100.01}
        paid = {"aplicacion_id": "APP-APRIL", "importe_usd": 30}
        result = self.selection([row], [self.deposit("MAY", [paid]), self.deposit("VOID", [paid], 2),
                                        self.deposit("DRAFT", [paid], 0)])
        self.assertEqual(result["rows"][0]["pending_cents"], 7000)
        # Show the raw core amount separately from the adjusted reconciliation balance.
        self.assertEqual(result["rows"][0]["applied_cents"], 10001)
        self.assertEqual(result["rows"][0]["assigned_cents"], 3000)
        self.assertEqual(result["available_cents"], 10000)

    def test_existing_manual_allocation_is_not_counted_twice(self):
        targets = [{"historical_application": "APP", "amount_usd": 40}]
        deposit = self.deposit("CURRENT", [{"aplicacion_id": "APP", "importe_usd": 40}])
        result = self.selection([{ "historical_application": "APP", "due_usd": 100}], [deposit], targets)
        self.assertEqual(result["available_cents"], 6000)
        self.assertEqual(result["rows"], [])

    def test_same_deposit_partial_automatic_payment_keeps_pending_selectable(self):
        for origin in (DETAIL, FIFO, REFERENCE, None):
            with self.subTest(origin=origin):
                row = {"historical_application": "APP", "due_usd": 18.67, "applied_usd": 18.67}
                deposit = self.deposit("CURRENT", [
                    {"aplicacion_id": "APP", "importe_usd": 0.01, "origen": origin},
                ])
                result = self.selection([row], [deposit], amount=18.68)
                self.assertEqual(len(result["rows"]), 1)
                self.assertEqual(result["rows"][0]["assigned_cents"], 1)
                self.assertEqual(result["rows"][0]["pending_cents"], 1866)
                self.assertEqual(result["available_cents"], 1867)

    def test_supplementing_automatic_payment_reserves_both_amounts(self):
        row = {"historical_application": "APP", "due_usd": 18.67}
        target = {"historical_application": "APP", "amount_usd": 18.66, "detail_row": "SECOND"}
        for manual_paid in (False, True):
            with self.subTest(manual_paid=manual_paid):
                detail = [{"aplicacion_id": "APP", "importe_usd": 0.01, "origen": FIFO}]
                if manual_paid:
                    detail.append({"aplicacion_id": "APP", "importe_usd": 18.66, "origen": MANUAL})
                result = self.selection([row], [self.deposit("CURRENT", detail)], [target], amount=18.68)
                self.assertEqual(result["reserved_cents"], 1867)
                self.assertEqual(result["available_cents"], 1)
                self.assertEqual(result["rows"], [])  # Already added to targets.

    def test_automatic_collection_and_complement_keep_their_remaining_balance(self):
        rows = [{"period": "APRIL", "row_key": "1", "due_usd": 90},
                {"complementary_item": "FEE", "due_usd": 10}]
        deposit = self.deposit("CURRENT", [
            {"periodo": "APRIL", "fila_id": "1", "importe_usd": 30, "origen": DETAIL},
            {"partida": "FEE", "importe_usd": 6, "origen": REFERENCE},
        ])
        result = self.selection(rows, [deposit])
        self.assertEqual([row["pending_cents"] for row in result["rows"]], [6000, 400])
        self.assertEqual(result["available_cents"], 6400)

    def test_fully_paid_automatic_application_is_not_selectable(self):
        row = {"historical_application": "APP", "due_usd": 18.67}
        deposit = self.deposit("CURRENT", [{"aplicacion_id": "APP", "importe_usd": 18.67, "origen": FIFO}])
        self.assertEqual(self.selection([row], [deposit])["rows"], [])

    def test_negative_manual_complement_is_not_double_counted_with_automatic_cash(self):
        deposit = self.deposit("CURRENT", [
            {"aplicacion_id": "APP", "importe_usd": 110, "origen": FIFO},
            {"partida": "SHORT", "importe_usd": -10, "origen": MANUAL},
        ])
        result = self.selection([], [deposit], [{"complementary_item": "SHORT", "amount_usd": -10}])
        self.assertEqual(result["reserved_cents"], 10000)
        self.assertEqual(result["available_cents"], 0)

    def test_automatic_allocations_and_rounding_also_consume_budget(self):
        deposit = self.deposit("CURRENT", [
            {"aplicacion_id": "AUTO", "importe_usd": 30},
            {"tipo": "Movimiento de conciliación", "importe_usd": 0.01},
        ])
        result = self.selection([], [deposit], [{"complementary_item": "FEE", "amount_usd": 10}])
        self.assertEqual(result["available_cents"], 5999)

    def test_collection_keys_include_period_and_fee_has_separate_balance(self):
        rows = [{"period": "APRIL", "row_key": "1", "due_usd": 90},
                {"period": "MAY", "row_key": "1", "due_usd": 90},
                {"complementary_item": "FEE", "due_usd": 10}]
        deposit = self.deposit("PAID", [{"periodo": "APRIL", "fila_id": "1", "importe_usd": 90},
                                        {"partida": "FEE", "importe_usd": 6}])
        result = self.selection(rows, [deposit])
        self.assertEqual([row["pending_cents"] for row in result["rows"]], [9000, 400])
        self.assertEqual(result["rows"][1]["assigned_cents"], 600)
        self.assertIsNone(result["rows"][1]["applied_cents"])

    def test_existing_targets_above_actual_allocation_reserve_full_instruction(self):
        deposit = self.deposit("CURRENT", [{"aplicacion_id": "APP", "importe_usd": 20}])
        result = self.selection([], [deposit], [{"historical_application": "APP", "amount_usd": 80}])
        self.assertEqual(result["available_cents"], 2000)

    def test_paid_cent_adjustment_does_not_reappear_as_pending(self):
        # The candidate loader subtracts a negative tolerance adjustment from due.
        deposit = self.deposit("PREVIOUS", [{"aplicacion_id": "APP", "importe_usd": 46.52}])
        result = self.selection([{"historical_application": "APP", "due_usd": 46.52}], [deposit])
        self.assertEqual(result["rows"], [])

    def test_cents_remain_exact_with_many_small_instructions(self):
        result = self.selection([], targets=[{"historical_application": str(i), "amount_usd": 0.01}
                                            for i in range(200)], amount=2.01)
        self.assertEqual(result["available_cents"], 1)


if __name__ == "__main__":
    unittest.main()
