import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import complementary_deposit_link as api
from credinomina_reconciliation.detail_balances import assert_detail_capacity, update_detail_balances
from credinomina_reconciliation.rounding import money


class ComplementaryDepositLinkTests(unittest.TestCase):
    def test_link_cannot_assign_more_than_detail_row_can_receive(self):
        row = frappe._dict(name="ROW", source_row=80, amount_usd=25.94, pending_usd=25.94)
        deposit = frappe._dict(name="DEP", modified="deposit-v1", docstatus=1, employer="AGROSACO",
                               detail_rows=[row], targets=[], amount_usd=2694.04)
        item = frappe._dict(name="CN-COMP-2026-00210", modified="item-v1", docstatus=1,
                            amount_usd=51.87, employer="AGROSACO")
        deposit.append = Mock(side_effect=lambda table, values: frappe._dict(name="TARGET", **values))
        deposit.save = Mock()
        deposit.add_comment = Mock()
        item.add_comment = Mock()
        with patch("credinomina_reconciliation.client_credit.lock_credit_deposit", return_value=deposit), \
             patch.object(api.frappe, "get_doc", return_value=item), \
             patch.object(api, "_check_item"), patch.object(api, "_check_deposit"), \
             patch.object(api, "_capacity", return_value=(money(51.87), money(167.58), False)), \
             patch("credinomina_reconciliation.detail_balances.update_detail_balances"):
            with self.assertRaisesRegex(frappe.ValidationError, "solo puede recibir US\\$ 25.94"):
                api.link(item.name, deposit.name, "item-v1", "deposit-v1", 51.87, detail_row=row.name)
            deposit.append.assert_not_called()
            deposit.save.assert_not_called()
            result = api.link(item.name, deposit.name, "item-v1", "deposit-v1", 25.94, detail_row=row.name)
        self.assertEqual(result["amount_usd"], 25.94)
        self.assertEqual(deposit.append.call_args.args[1]["detail_row"], row.name)

    def test_saved_row_capacity_rejects_negative_pending(self):
        row = frappe._dict(name="ROW", source_row=80, amount_usd=25.94)
        document = frappe._dict(docstatus=0, detail_rows=[row], targets=[
            frappe._dict(name="TARGET", detail_row="ROW", complementary_item="CN-COMP-2026-00210",
                         amount_usd=51.87)])
        update_detail_balances(document)
        self.assertEqual(row.pending_usd, -25.93)
        with self.assertRaisesRegex(frappe.ValidationError, "Fila 80"):
            assert_detail_capacity(document)
        document.targets[0].amount_usd = 25.94
        update_detail_balances(document)
        assert_detail_capacity(document)
        self.assertEqual(row.pending_usd, 0)
