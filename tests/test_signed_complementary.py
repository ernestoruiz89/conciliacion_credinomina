import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.allocation import allocate_cash
from credinomina_reconciliation.remittance_selection import pending_selection
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item.cn_complementary_item import CNComplementaryItem


class SignedComplementaryTests(unittest.TestCase):
    def reconcile(self, deposit=90, application=100, adjustment=-10, first_application=100):
        deposits = [{"id": "DEP", "amount_usd": deposit, "group": "EMP"}]
        claims = [{"id": "H:APP", "kind": "H", "amount_usd": application, "group": "EMP"},
                  {"id": "X:COMP", "kind": "X", "amount_usd": adjustment, "group": "EMP"}]
        instructions = [{"id": "T1", "deposit_id": "DEP", "claim_id": "H:APP", "amount_usd": first_application},
                        {"id": "T2", "deposit_id": "DEP", "claim_id": "X:COMP", "amount_usd": adjustment}]
        return allocate_cash(deposits, claims, instructions)

    def test_short_deposit_uses_negative_adjustment_without_inventing_net_cash(self):
        result = self.reconcile()
        self.assertEqual(result["deposit_remaining"]["DEP"], 0)
        self.assertEqual(result["claim_remaining"], {"H:APP": 0, "X:COMP": 0})
        self.assertEqual(sum(row["amount_usd"] for row in result["allocations"]), 90)
        self.assertEqual(result["instruction_results"], {"T1": "Aplicada", "T2": "Aplicada"})

    def test_excess_deposit_keeps_positive_complement(self):
        result = self.reconcile(deposit=100, application=90, adjustment=10, first_application=90)
        self.assertEqual(result["deposit_remaining"]["DEP"], 0)
        self.assertEqual(sum(row["amount_usd"] for row in result["allocations"]), 100)

    def test_invalid_signed_group_is_atomic(self):
        # Failure of the application must not leave a negative allocation that creates capacity.
        result = self.reconcile(application=50)
        self.assertEqual(result["allocations"], [])
        self.assertEqual(result["deposit_remaining"]["DEP"], 90)

    def test_negative_only_does_not_increase_deposit_balance(self):
        result = allocate_cash([{"id": "D", "amount_usd": 90}],
                               [{"id": "X:C", "kind": "X", "amount_usd": -10}],
                               [{"id": "T", "deposit_id": "D", "claim_id": "X:C", "amount_usd": -10}])
        self.assertEqual(result["allocations"], [])
        self.assertEqual(result["deposit_remaining"]["D"], 90)

    def test_negative_claim_is_not_used_automatically(self):
        result = allocate_cash([{"id": "D", "amount_usd": 90, "reference": "REF"}],
                               [{"id": "X:C", "kind": "X", "amount_usd": -10, "references": ["REF"]}])
        self.assertEqual(result["allocations"], [])

    def test_same_negative_adjustment_cannot_be_consumed_twice(self):
        result = allocate_cash([{"id": name, "amount_usd": 90} for name in ("D1", "D2")],
                               [{"id": "X:C", "kind": "X", "amount_usd": -10},
                                {"id": "H:A", "kind": "H", "amount_usd": 200}],
                               [{"id": name + suffix, "deposit_id": name, "claim_id": claim, "amount_usd": amount}
                                for name in ("D1", "D2") for suffix, claim, amount in (("a", "H:A", 100), ("b", "X:C", -10))])
        self.assertEqual(result["deposit_remaining"], {"D1": 0, "D2": 90})
        self.assertEqual(len(result["allocations"]), 2)

    def test_picker_includes_negative_target_once(self):
        target = {"complementary_item": "COMP", "amount_usd": -10}
        result = pending_selection([], [], "DEP", 90, [target])
        self.assertEqual(result["available_cents"], 10000)
        result = pending_selection([], [{"name": "DEP", "docstatus": 1, "allocation_detail": [
            {"partida": "COMP", "importe_usd": -10}, {"aplicacion_id": "APP", "importe_usd": 100},
        ]}], "DEP", 90, [target, {"historical_application": "APP", "amount_usd": 100}])
        self.assertEqual(result["available_cents"], 0)

    def document(self, **overrides):
        return SimpleNamespace(amount=-365.5, currency="NIO", fx_rate=36.55,
                               reference="REF", voucher="", voucher_line="", doctype="CN Complementary Item",
                               name="COMP", period=None, employer="EMP", **overrides)

    def test_no_voucher_is_pending_and_does_not_collide_with_other_pending_items(self):
        doc = self.document()
        with patch.object(frappe, "db", Mock()) as db:
            CNComplementaryItem.validate(doc)
            db.get_value.assert_not_called()
        self.assertEqual(doc.amount_usd, -10)
        self.assertEqual(doc.accounting_status, "Pendiente de registro")
        doc.voucher = " AS-100 "
        with patch.object(frappe, "db", Mock()) as db:
            db.get_value.return_value = None
            CNComplementaryItem.before_update_after_submit(SimpleNamespace(validate=lambda: CNComplementaryItem.validate(doc)))
        self.assertEqual(doc.voucher, "AS-100")
        self.assertEqual(doc.accounting_status, "Registrada")
