import json
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import reconciliation_trace as trace
from credinomina_reconciliation.conciliacion_credinomina.report.transacciones_por_empresa import transacciones_por_empresa as report
from credinomina_reconciliation.patches.v1_0 import backfill_reconciliation_trace as migration


class ReconciliationTraceTests(unittest.TestCase):
    def version(self, name, user, old, new, deposit="D1"):
        return frappe._dict(name=name, owner=user, creation="2026-10-30 10:00:00", ref_doctype=trace.DEPOSIT,
            docname=deposit, data=json.dumps({"cn_reconciliation": {"action": "Conciliar",
                "before": {"allocation_detail": old}, "after": {"allocation_detail": new}}}))

    def allocation(self, amount, application="A", detail="R1"):
        return dict(tipo="Aplicacion historica", aplicacion_id=application, importe_usd=amount, fila_detalle=detail)

    def test_each_allocation_keeps_its_user_and_reversals_preserve_prior_events(self):
        with patch.object(trace, "append") as append:
            trace.capture_version(self.version("V1", "Ana", [], [self.allocation(30)]))
            trace.capture_version(self.version("V2", "Luis", [], [self.allocation(20)], "D2"))
            trace.capture_version(self.version("V3", "Ana", [self.allocation(30)], [self.allocation(10)]))
        events = [call for call in append.call_args_list if call.args[2] == trace.APPLICATION]
        self.assertEqual([(call.args[1], call.args[3], call.kwargs["user"],
            call.kwargs["after"] - call.kwargs["before"]) for call in events],
            [("D1", "A", "Ana", 30), ("D2", "A", "Luis", 20), ("D1", "A", "Ana", -20)])

    def test_details_are_separate_and_cosmetic_changes_do_not_create_events(self):
        saved = []
        def save(*args, **kwargs):
            if trace.money(kwargs.get("before")) != trace.money(kwargs.get("after")):
                saved.append((args, kwargs))
        old = [self.allocation(20), self.allocation(10, detail="R2"), self.allocation(50, "OTHER")]
        new = [dict(old[2], excepciones_heredadas=["comment"]), self.allocation(30), self.allocation(5, detail="R2")]
        with patch.object(trace, "append", side_effect=save):
            trace.capture_version(self.version("V", "Ana", old, new))
            trace.capture_version(frappe._dict(ref_doctype=trace.DEPOSIT, data='{"changed":[["comments","a","b"]]}'))
        self.assertEqual([(row[0][3], row[1]["detail_row"], row[1]["after"] - row[1]["before"]) for row in saved],
                         [("A", "R1", 10), ("A", "R2", -5)])

    def test_appending_is_idempotent_and_keeps_initial_attribution_without_amount(self):
        doc = Mock()
        with patch.object(frappe.db, "exists", side_effect=[False, True]), patch.object(frappe, "get_doc", return_value=doc) as create:
            for _ in range(2):
                trace.append("CN Accounting Import", "I", trace.APPLICATION, "A", key="initial-last-editor",
                    user="last-editor", timestamp="2026-10-01", origin=trace.INITIAL)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(create.call_args.args[0]["performed_by"], "last-editor")
        self.assertEqual(create.call_args.args[0]["delta_usd"], 0)
        self.assertEqual(doc.insert.call_args.kwargs["set_name"], create.call_args.args[0]["name"])

    def test_journals_keep_original_actor_even_when_another_user_saves(self):
        item = frappe._dict(doctype=trace.ITEM, name="I", docstatus=1,
            compensations=[dict(entry_key="O1", confirmed_by="Ana", confirmed_on="2026-10-01", amount_usd=15, counterpart="X")],
            application_adjustment_history=json.dumps([dict(related_application="A", recorded_by="Luis",
                recorded_on="2026-10-02", application_adjustment_usd=5)]),
            credit_history=json.dumps([dict(application_id="A", operation_id="C1", usuario="Ana",
                registrado_el="2026-10-03", importe_usd=10), dict(application_id="A", operation_id="C2", usuario="Luis",
                registrado_el="2026-10-04", importe_usd=-10)]))
        with patch.object(trace, "append") as append:
            trace.capture_item_journals(item)
            trace.capture_adjustment_additions(item, trace.entries(item.application_adjustment_history))
        self.assertEqual([(call.kwargs["user"], call.kwargs["after"]) for call in append.call_args_list],
                         [("Ana", 15), ("Ana", 10), ("Luis", -10), ("Luis", 5)])

    def test_fifo_tracks_only_the_explicit_portion_for_each_application(self):
        row = dict(tipo="Cobranza", periodo="P", fila_id="C", importe_usd=50, fila_detalle="D1",
            aplicaciones_fifo=[dict(application_id="A", amount_usd=30), dict(application_id="B", amount_usd=20)])
        with patch.object(trace, "append") as append:
            trace.capture_version(self.version("V", "Ana", [], [row]))
        calls = [call for call in append.call_args_list if call.args[2] == trace.APPLICATION]
        self.assertEqual([(call.args[3], call.kwargs["after"]) for call in calls], [("A", 30), ("B", 20)])

    def test_permission_scoped_history_and_shared_collection_do_not_expose_other_targets(self):
        rows = [frappe._dict(name="T1", source_doctype=trace.DEPOSIT, source_name="VISIBLE", target_doctype=trace.COLLECTION,
            target_name="C", performed_by="Ana", performed_on="2026-10-01", delta_usd=30),
            frappe._dict(name="T2", source_doctype=trace.DEPOSIT, source_name="HIDDEN", target_doctype=trace.APPLICATION,
            target_name="A", performed_by="Luis", performed_on="2026-10-02", delta_usd=20)]
        with patch.object(trace, "_pages", return_value=rows), patch.object(frappe, "has_permission", return_value=True), \
             patch.object(frappe, "get_list", return_value=["VISIBLE"]):
            result = trace.histories("Aplicaciones", [dict(name="A", collection_row_id="C")], {"C": {}})
        self.assertEqual(len(result["A"]), 1)
        self.assertTrue(result["A"][0]["shared_collection"])
        self.assertEqual(result["A"][0]["performed_by"], "Ana")
        self.assertNotIn("target_name", result["A"][0])

    def test_user_filter_counts_transaction_once_and_combines_state_totals_and_pagination(self):
        sources = [frappe._dict(name=str(i), employer="E", deposit_date="2025-04-30", docstatus=1,
            amount_usd=100, allocated_usd=50, unclassified_usd=50) for i in range(106)]
        records = [dict(name=row.name, employer="E", date=row.deposit_date,
                       state="Pendiente" if i == 0 else "Parcial") for i, row in enumerate(sources)]
        def histories(kind, rows, collections, user=None):
            # Two operations by the same user never multiply transaction counts.
            return {row.name: [dict(performed_by="Ana"), dict(performed_by="Ana")] for row in rows if row.name != "1"}
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(report, "load_transactions", return_value=(records, sources, {})), \
             patch.object(trace, "histories", side_effect=histories):
            _, matrix, _, _, summary = report.execute(dict(year=2025, transaction_type="Depósitos", reconciled_by="Ana"))
            first = report.get_month_detail(2025, 4, "E", "Depósitos", reconciled_by="Ana")
            second = report.get_month_detail(2025, 4, "E", "Depósitos", reconciled_by="Ana", start=100, only_differences=1)
            empty = report.get_month_detail(2025, 4, "E", "Depósitos", reconciled_by="Ana", state="Pendiente")
        self.assertEqual(matrix[0]["m04"], 104)
        self.assertEqual(matrix[0]["_detail_filters"]["reconciled_by"], "Ana")
        self.assertEqual(summary[1]["value"], 104)
        self.assertEqual((len(first["rows"]), len(second["rows"])), (100, 4))
        self.assertEqual(first["totals"]["original_usd"], 10400)
        self.assertEqual(first["summary"][2]["value"], 0)
        self.assertEqual(empty["rows"], [])

    def test_patch_uses_parent_last_editor_only_for_settled_or_partial_rows(self):
        records = [dict(name="A", state="Conciliado"), dict(name="P", state="Pendiente")]
        sources = [dict(name="A", parent="IMPORT"), dict(name="P", parent="IMPORT")]
        editor = frappe._dict(name="IMPORT", modified_by="last-editor", modified="2026-09-30")
        with patch.object(frappe, "reload_doc"), patch.object(report, "TRANSACTION_DOCTYPES", {"Aplicaciones": "CN Accounting Import"}), \
             patch.object(report, "load_transactions", return_value=(records, sources, {})), \
             patch.object(report, "_application_progress"), patch.object(frappe, "get_all", return_value=[editor]), \
             patch.object(trace, "append") as append, patch.object(frappe.db, "set_value") as mutate:
            migration.execute()
        mutate.assert_not_called()
        self.assertEqual(append.call_count, 1)
        self.assertEqual(append.call_args.args, ("CN Accounting Import", "IMPORT", trace.APPLICATION, "A"))
        self.assertEqual(append.call_args.kwargs["user"], "last-editor")
        self.assertEqual(append.call_args.kwargs["origin"], trace.INITIAL)
