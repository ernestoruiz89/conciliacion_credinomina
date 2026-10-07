import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.detail_balances import linked_balance, update_detail_balances
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.patches.v1_0 import separate_period_and_deposit_balances as migration


class DetailBalancesTests(unittest.TestCase):
    def test_grid_displays_amounts_and_allows_submitted_updates(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_detail/cn_remittance_detail.json").read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in schema["fields"]}
        names = ["client_name", "loan_number", "amount_usd", "linked_usd", "pending_usd", "match_status"]
        self.assertEqual([name for name in schema["field_order"] if name in names], names)
        self.assertTrue(fields["client_credit_usd"]["read_only"] and fields["client_credit_usd"]["allow_on_submit"])
        self.assertTrue(fields["company_credit_usd"]["read_only"] and fields["company_credit_usd"]["allow_on_submit"])
        self.assertEqual(sum(fields[name]["columns"] for name in names), 10)
        for name in ("linked_usd", "pending_usd"):
            self.assertTrue(fields[name]["read_only"] and fields[name]["allow_on_submit"] and fields[name]["in_list_view"])

    def test_partial_manual_is_visible_without_becoming_settled(self):
        row = frappe._dict(name="R", amount_usd=165.16, matched_targets="[]", match_status="Revisar")
        update_detail_balances({"detail_rows": [row], "targets": [
            {"detail_row": "R", "amount_usd": 147.95}, {"detail_row": "OTHER", "amount_usd": 100},
            {"amount_usd": 100}]})
        self.assertEqual((row.linked_usd, row.pending_usd), (147.95, 17.21))
        self.assertEqual(row.match_status, "Revisar")

    def test_no_double_count_and_removed_manual_is_not_resurrected(self):
        old = '[{"instruction_id":"T", "amount_usd":100}]'
        self.assertEqual(linked_balance(100, [{"amount_usd":100}], old)["pending_usd"], 0)
        self.assertEqual(linked_balance(100, [], old)["pending_usd"], 100)
        self.assertEqual(linked_balance(100, [], '[{"amount_usd":100}]')["pending_usd"], 0)

    def test_signed_complements_overlink_and_cents(self):
        self.assertEqual(linked_balance(90, [{"amount_usd":100}, {"amount_usd":-10}], [])["pending_usd"], 0)
        self.assertEqual(linked_balance(90, [{"amount_usd":100}], [])["pending_usd"], -10)
        self.assertEqual(linked_balance(.3, [], '[{"amount_usd":0.1},{"amount_usd":0.2}]')["pending_usd"], 0)
        self.assertEqual(linked_balance(10, [], '{bad}')["pending_usd"], 10)

    def test_historical_partial_does_not_become_excess_due_to_unassigned_cash(self):
        period = frappe._dict(name="P", reconciliation_mode="Historica", status="Pendiente")
        row = frappe._dict(name="A", event_type="Aplicacion", effective=1, historical_period="P",
                          match_status="Conciliado", amount=1624.96)
        account = frappe._dict(unclassified_usd=118.99, reference="D", voucher="V", event_date="2025-05-27")
        allocation = {"allocations": [{"claim_id":"H:A", "deposit_id":"D", "amount_usd":1482.64, "origin":"Manual"}],
                      "deposit_meta":{"D":{"account":account}}, "rounding_movements":[]}
        with patch.object(engine, "_save_reconciled_document"):
            engine._rebuild_historical_balances([period], [row], allocation)
        self.assertEqual(period.status, "Parcial")
        self.assertEqual(period.unassigned_deposit_usd, 118.99)
        self.assertEqual(row.historical_balance_usd, 142.32)

    def test_patch_preserves_closed_status_and_amounts(self):
        periods = [frappe._dict(name="P", status="Con excedente", reconciliation_mode="Historica",
                               applied_usd=1624.96, remitted_usd=1482.64, rounding_adjustment_usd=0),
                   frappe._dict(name="C", status="Cerrado", applied_usd=100, remitted_usd=100)]
        deposit = frappe._dict(name="D", docstatus=1, unclassified_usd=118.99,
                              allocation_detail='[{"tipo":"Aplicacion historica","periodo":"P"},{"tipo":"Aplicacion historica","periodo":"P"}]')
        batches = lambda doctype, fields: [[deposit]] if doctype == "CN Remittance Allocation" else [periods]
        def update(doctype, name, values, **kwargs):
            self.assertFalse(kwargs["update_modified"])
            self.assertLessEqual(set(values), {"status", "unassigned_deposit_usd"})
            next(row for row in periods if row.name == name).update(values)
        with patch.object(migration, "_batches", side_effect=batches), \
             patch.object(migration.frappe, "get_all", return_value=[]), \
             patch.object(migration.frappe, "db", Mock(set_value=Mock(side_effect=update))):
            migration.execute()
            migration.execute()
        self.assertEqual(periods[0].status, "Parcial")
        self.assertEqual(periods[0].unassigned_deposit_usd, 118.99)
        self.assertEqual(periods[0].remitted_usd, 1482.64)
        self.assertEqual(periods[1].status, "Cerrado")
