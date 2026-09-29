import unittest

from credinomina_reconciliation.remittance_selection import pending_selection


class PendingRemittanceSelectionTests(unittest.TestCase):
    def selection(self, rows, deposits=(), targets=(), amount=100):
        return pending_selection(rows, deposits, "CURRENT", amount, targets)

    def deposit(self, name, detail, status=1):
        return {"name": name, "docstatus": status, "allocation_detail": detail}

    def test_partial_historical_payment_and_cancelled_deposit(self):
        row = {"historical_application": "APP-APRIL", "due_usd": 100}
        paid = {"aplicacion_id": "APP-APRIL", "importe_usd": 30}
        result = self.selection([row], [self.deposit("MAY", [paid]), self.deposit("VOID", [paid], 2)])
        self.assertEqual(result["rows"][0]["pending_cents"], 7000)
        self.assertEqual(result["available_cents"], 10000)

    def test_existing_manual_allocation_is_not_counted_twice(self):
        targets = [{"historical_application": "APP", "amount_usd": 40}]
        deposit = self.deposit("CURRENT", [{"aplicacion_id": "APP", "importe_usd": 40}])
        result = self.selection([{ "historical_application": "APP", "due_usd": 100}], [deposit], targets)
        self.assertEqual(result["available_cents"], 6000)
        self.assertEqual(result["rows"], [])

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
