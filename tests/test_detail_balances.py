import unittest
import json
from types import SimpleNamespace
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
        names = ["client_name", "loan_number", "amount_usd", "linked_total_usd", "pending_usd", "match_status"]
        self.assertEqual([name for name in schema["field_order"] if name in names], names)
        self.assertTrue(fields["client_credit_usd"]["read_only"] and fields["client_credit_usd"]["allow_on_submit"])
        self.assertTrue(fields["company_credit_usd"]["read_only"] and fields["company_credit_usd"]["allow_on_submit"])
        self.assertEqual(sum(fields[name]["columns"] for name in names), 10)
        self.assertTrue(fields["linked_total_usd"]["read_only"] and fields["linked_total_usd"]["is_virtual"] and fields["linked_total_usd"]["in_list_view"])
        self.assertTrue(fields["pending_usd"]["read_only"] and fields["pending_usd"]["allow_on_submit"] and fields["pending_usd"]["in_list_view"])
        self.assertTrue(fields["linked_usd"]["read_only"] and fields["linked_usd"]["allow_on_submit"])

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

    def test_documented_difference_review_preserves_amounts_and_settled_status(self):
        for status, expected in (("Diferencia documentada", "Revisar: Diferencia documentada"),
                                 ("Conciliada", "Conciliada")):
            row = frappe._dict(name="DIFF", row_kind="Diferencia documentada", match_status=status,
                amount_usd=-.01, linked_usd=0, pending_usd=-.01, difference_core_pending_usd=.01,
                matched_targets="[]")
            before = dict(row)
            document = dict(detail_rows=[row], targets=[])
            update_detail_balances(document)
            update_detail_balances(document)
            self.assertEqual(dict(row), {**before, "match_status": expected})

    def test_reclassification_links_survive_manual_edits_without_double_counting(self):
        history = '[{"instruction_id":"RECLASS:one", "amount_usd":2}, {"instruction_id":"old", "amount_usd":7}]'
        self.assertEqual(linked_balance(10, [], history), {'linked_usd': 2, 'pending_usd': 8})
        self.assertEqual(linked_balance(10, [{'amount_usd': 3}], history), {'linked_usd': 5, 'pending_usd': 5})
        self.assertEqual(linked_balance(10, [{'id': 'RECLASS:one', 'amount_usd': 2}], history),
                         {'linked_usd': 2, 'pending_usd': 8})

    def test_signed_complements_overlink_and_cents(self):
        self.assertEqual(linked_balance(90, [{"amount_usd":100}, {"amount_usd":-10}], [])["pending_usd"], 0)
        self.assertEqual(linked_balance(90, [{"amount_usd":100}], [])["pending_usd"], -10)
        self.assertEqual(linked_balance(.3, [], '[{"amount_usd":0.1},{"amount_usd":0.2}]')["pending_usd"], 0)
        self.assertEqual(linked_balance(10, [], '{bad}')["pending_usd"], 10)

    def test_confirmed_fifo_survives_new_targets_and_retained_cache_without_double_counting(self):
        fifo = dict(claim_id="H:OLD", amount_usd=34.59, fifo_applications=[dict(application_id="OLD", amount_usd=34.59)])
        manual = [dict(historical_application="NEW1", amount_usd=34.59), dict(historical_application="NEW2", amount_usd=34.59)]
        for history in ([fifo], [fifo | {"instruction_id": "RETAINED:D:R:H:OLD"}]):
            self.assertEqual(linked_balance(71.51, manual, history), dict(linked_usd=103.77, pending_usd=-32.26))
            self.assertEqual(linked_balance(71.51, [], history), dict(linked_usd=34.59, pending_usd=36.92))
            self.assertEqual(linked_balance(71.51, [dict(id="M", claim_id="H:OLD", amount_usd=34.59)], history),
                dict(linked_usd=34.59, pending_usd=36.92))
            self.assertEqual(linked_balance(71.51, [dict(claim_id="H:OLD", amount_usd=10)], history)["linked_usd"], 34.59)
        # Frappe child Documents support .get(), not dictionary subscripting.
        target = SimpleNamespace(get=dict(historical_application="NEW1", amount_usd=34.59).get)
        self.assertEqual(linked_balance(71.51, [target], [fifo]), dict(linked_usd=69.18, pending_usd=2.33))

    def test_saved_ledger_wins_over_empty_cache_and_preview_does_not_change_cash(self):
        row = frappe._dict(name="R", amount_usd=71.51, matched_targets="[]")
        ledger = json.dumps([dict(tipo="Aplicacion historica", aplicacion_id="OLD", fila_detalle="R",
            importe_usd=34.59, origen="Automática FIFO")])
        document = dict(detail_rows=[row], allocation_detail=ledger,
            targets=[dict(detail_row="R", historical_application="NEW1", amount_usd=34.59),
                     dict(detail_row="R", historical_application="NEW2", amount_usd=34.59)])
        update_detail_balances(document)
        self.assertEqual((row.linked_usd, row.pending_usd), (103.77, -32.26))
        self.assertEqual(document["allocation_detail"], ledger)
        document["targets"] = [dict(detail_row="R", historical_application="NEW1", amount_usd=34.59, result="Aplicada")]
        update_detail_balances(document)
        self.assertEqual((row.linked_usd, row.pending_usd), (69.18, 2.33))
        document["targets"] = []
        update_detail_balances(document)
        self.assertEqual((row.linked_usd, row.pending_usd), (34.59, 36.92))

    def test_historical_partial_does_not_become_excess_due_to_unassigned_cash(self):
        period = frappe._dict(name="P", reconciliation_mode="Historica", status="Pendiente")
        row = frappe._dict(name="A", event_type="Aplicacion", effective=1, historical_period="P",
                          match_status="Conciliado", amount=1624.96)
        account = frappe._dict(unclassified_usd=118.99, reference="D", voucher="V", event_date="2025-05-27")
        allocation = {"allocations": [{"claim_id":"H:A", "deposit_id":"D", "amount_usd":1482.64, "origin":"Manual"}],
                      "deposit_meta":{"D":{"account":account}}, "rounding_movements":[]}
        with patch.object(engine, "_save_reconciled_document"), \
             patch("credinomina_reconciliation.credit_application.load_allocations", return_value=[]):
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
