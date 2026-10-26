import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import complementary_state as service
from credinomina_reconciliation.patches.v1_0 import backfill_complementary_reconciliation_status as migration


class Callbacks:
    def __init__(self):
        self.callbacks = []

    def add(self, callback):
        self.callbacks.append(callback)


def item(name="C", **values):
    return frappe._dict(dict(name=name, category="Ajuste de aplicación", docstatus=1,
        amount_usd=100, related_application="APP", application_adjustment_usd=40, reconciliation_status="Pendiente") | values)


class ComplementaryStateTests(unittest.TestCase):
    def setUp(self):
        service._reset()
        self.addCleanup(service._reset)

    def test_states_match_financial_rules_and_indicator_overrides(self):
        for values, expected in [({}, "Parcial"), ({"application_adjustment_usd": 100}, "Conciliado"),
            ({"application_adjustment_usd": 0}, "Pendiente"), ({"docstatus": 2}, "Cancelada"),
            ({"status": "Revertido"}, "Revertida"), ({"origin_deposit": "DEP"}, "Vinculado a depósito"),
            ({"application_adjustment_usd": 101}, "Revisar distribución"),
            ({"category": "Compensación entre partidas", "receivable_origin": "ORIGIN", "compensated_usd": 40}, "Parcialmente revertida")]:
            with self.subTest(values=values):
                self.assertEqual(service.state(item(**values)), expected)
        self.assertEqual(service.state(item(category="Saldo a favor del cliente", result="Saldo a favor documentado",
            credit_pending_usd=100, credit_management_status="Pendiente")), "Conciliado")
        self.assertEqual(service.state(item(origin_deposit="DEP", accounting_source_key="CORE",
            source_currency="USD", source_debit=200)), "Vinculado a depósito")

    def test_refresh_changes_only_cached_state_and_is_idempotent(self):
        rows = [item(), item("SAME", reconciliation_status="Parcial")]
        original = [dict(row) for row in rows]
        with patch.object(frappe.db, "set_value") as write, patch.object(frappe, "clear_document_cache"):
            self.assertEqual(service.refresh(rows), 1)
        write.assert_called_once_with(service.DOCTYPE, "C", service.FIELD, "Parcial", update_modified=False)
        self.assertEqual(rows, original)

    def test_cash_uses_complete_internal_journals(self):
        row = item(category="Otros ingresos")
        with patch.object(service, "_distribution_index", return_value={"C": [dict(amount_usd=100)]}) as read, \
             patch.object(frappe.db, "set_value") as write, patch.object(frappe, "clear_document_cache"):
            service.refresh([row])
        read.assert_called_once_with({"C"}, authoritative=True)
        self.assertEqual(write.call_args.args[3], "Conciliado")

    def test_bad_journal_does_not_block_other_items_or_look_like_zero(self):
        rows = [item("BAD", category="Otros ingresos"), item("GOOD", category="Otros ingresos")]
        with patch.object(service, "_distribution_index", side_effect=[ValueError("broken"), ValueError("broken"),
                {"GOOD": [dict(amount_usd=100)]}]), patch.object(frappe.db, "set_value") as write, \
             patch.object(frappe, "clear_document_cache"):
            service.refresh(rows)
        self.assertEqual([(call.args[1], call.args[3]) for call in write.call_args_list],
                         [("BAD", "Saldo no disponible"), ("GOOD", "Conciliado")])

    def test_events_batch_items_and_both_sides_of_changed_deposit(self):
        db = SimpleNamespace(before_commit=Callbacks(), after_commit=Callbacks(), after_rollback=Callbacks(), has_column=lambda *_: True)
        with patch.object(frappe.local, "db", db), patch.object(frappe, "get_all", side_effect=[["CREDIT"], [item()]]) as read, \
             patch.object(service, "refresh") as refresh:
            service.item_changed(frappe._dict(name="C"))
            service.deposit_changed(frappe._dict(name="DEP", allocation_detail=json.dumps([
                dict(tipo="Partida complementaria", partida="NEW")])), previous=dict(allocation_detail=json.dumps([
                dict(tipo="Partida complementaria", partida="OLD")])))
            self.assertEqual(len(db.before_commit.callbacks), 1)
            db.before_commit.callbacks[0]()
            self.assertEqual(read.call_args.kwargs["filters"]["name"], ["in", ["C", "CREDIT", "NEW", "OLD"]])
            refresh.assert_called_once()
            self.assertIsNone(frappe.flags.cn_complementary_states)
            db.after_rollback.callbacks[0]()
            self.assertFalse(frappe.flags.cn_complementary_states_scheduled)

    def test_queue_is_safe_before_schema_sync_and_on_rollback(self):
        db = SimpleNamespace(before_commit=Callbacks(), after_commit=Callbacks(), after_rollback=Callbacks(), has_column=lambda *_: False)
        with patch.object(frappe.local, "db", db), patch.object(frappe, "get_all") as read:
            service.mark("C")
            db.before_commit.callbacks[0]()
            read.assert_not_called()
            service.mark("OTHER")
            db.after_rollback.callbacks[0]()
            self.assertIsNone(frappe.flags.cn_complementary_states)

    def test_patch_uses_keyset_batches_without_reconciling_or_saving_documents(self):
        rows = [item("A"), item("B")]
        with patch.object(frappe, "reload_doc") as schema, patch.object(frappe, "get_all", side_effect=[rows, []]) as read, \
             patch.object(migration, "refresh") as refresh, patch.object(frappe, "get_doc") as get_doc:
            migration.execute()
        schema.assert_called_once_with("conciliacion_credinomina", "doctype", "cn_complementary_item")
        self.assertEqual(read.call_args.kwargs["filters"], {"name": [">", "B"]})
        refresh.assert_called_once_with(rows)
        get_doc.assert_not_called()
