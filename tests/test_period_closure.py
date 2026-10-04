"""Scoped checks must retain late, shared and invalid-target evidence."""
import json
import unittest
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import period_closure as closure
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as controller
from credinomina_reconciliation.patches.v1_0 import index_period_closure_links as indexes


class ClosureScopeTests(unittest.TestCase):
    def setUp(self):
        self.period = SimpleNamespace(name="PER", employer="A", collection_rows=[SimpleNamespace(name="COL")])
        self.scope = closure.ClosureScope(self.period, ["A", "PAYER", "SHARED"])
        self.data = defaultdict(list)
        self.calls = []

    def query(self, doctype, *, filters, pluck=None, fields=None, **kwargs):
        self.calls.append((doctype, filters))
        def matches(row):
            for field, expected in filters.items():
                value = row.get(field)
                if isinstance(expected, list):
                    operator, rhs = expected
                    if operator == "in" and value not in rhs:
                        return False
                    if operator == "!=" and value == rhs:
                        return False
                    if operator == ">" and (value or 0) <= rhs:
                        return False
                elif value != expected:
                    return False
            return True
        rows = [row for row in self.data[doctype] if matches(row)]
        return [row.get(pluck) for row in rows] if pluck else rows

    def exists(self, doctype, filters):
        return bool(self.query(doctype, filters=filters))

    def deposit(self, name, company="A", *, status=1, allocation=None, detail="Revisar filas"):
        self.data["CN Remittance Allocation"].append(frappe._dict(name=name, employer=company,
            docstatus=status, allocation_detail=json.dumps(allocation or []), detail_status=detail, unclassified_usd=0))

    def target(self, parent, **values):
        self.data["CN Remittance Target"].append(frappe._dict(parent=parent,
            parenttype="CN Remittance Allocation", result="Pendiente", **values))

    def test_related_evidence_uses_pool_and_explicit_links_not_dates(self):
        # Payer and shared-complement pool may contain deposits in later months.
        for name, company in [("SELECTED", "A"), ("HIST", "PAYER"), ("AUTO", "PAYER"),
                              ("COMP", "SHARED"), ("OUTSIDE", "OLD-PAYER"), ("OTHER", "A")]:
            self.deposit(name, company)
        self.data["CN Remittance Allocation"][2].allocation_detail = '[{"periodo":"PER"}]'
        self.data["CN Remittance Allocation"][3].allocation_detail = '[{"partida":"X"}]'
        self.data["CN Remittance Period"].append(frappe._dict(period="PER", parent="SELECTED",
            parenttype="CN Remittance Allocation", parentfield="detail_periods"))
        self.data["CN Source Row"].append(frappe._dict(name="H", historical_period="PER", event_type="Aplicacion", effective=1))
        self.data["CN Complementary Item"].append(frappe._dict(name="X", period="PER", docstatus=1))
        self.target("HIST", historical_application="H")
        self.target("OUTSIDE", period="PER")
        self.deposit("DRAFT", status=0); self.target("DRAFT", period="PER")
        self.deposit("CANCELLED", status=2); self.target("CANCELLED", period="PER")
        with patch.object(frappe, "get_all", side_effect=self.query):
            actual = {row.name for row in self.scope.related_deposits()}
            self.assertEqual(actual, {"SELECTED", "HIST", "AUTO", "COMP", "OUTSIDE"})
            self.assertEqual(controller._pending_remittance_details_for_period(self.period, self.scope), sorted(actual))
            previous_calls = len(self.calls)
            self.scope.related_deposits()
            self.assertEqual(len(self.calls), previous_calls, "Cache within one closure request only")
        for doctype, filters in self.calls:
            if doctype == "CN Remittance Allocation":
                self.assertTrue("employer" in filters or "name" in filters)
                self.assertEqual(filters["docstatus"], 1)
                self.assertNotIn("deposit_date", filters, "Late payments must remain visible")

    def test_ineffective_application_and_cancelled_item_do_not_hide_invalid_detail(self):
        self.deposit("INVALID-H"); self.deposit("INVALID-X")
        self.data["CN Source Row"].append(frappe._dict(name="H", historical_period="PER", event_type="Aplicacion", effective=0))
        self.data["CN Complementary Item"].append(frappe._dict(name="X", period="PER", docstatus=2))
        self.target("INVALID-H", historical_application="H")
        self.target("INVALID-X", complementary_item="X")
        with patch.object(frappe, "get_all", side_effect=self.query):
            self.assertEqual(self.scope.application_ids(), [])
            self.assertEqual({row.name for row in self.scope.related_deposits()}, {"INVALID-H", "INVALID-X"})

    def test_grouped_operative_application_scans_only_pool_imports(self):
        for name, company in [("I", "A"), ("UNRELATED", "Z")]:
            self.data["CN Accounting Import"].append(frappe._dict(name=name, employer=company))
        row = frappe._dict(parent="UNRELATED", parenttype="CN Accounting Import", effective=1,
            event_type="Aplicacion", application_allocation_detail='[{"collection_row_id":"COL"}]')
        self.data["CN Source Row"].append(row)
        with patch.object(frappe, "get_all", side_effect=self.query), \
             patch.object(frappe, "db", SimpleNamespace(exists=self.exists)):
            self.assertFalse(self.scope.has_operative_application())
            row.parent = "I"
            self.assertTrue(self.scope.has_operative_application())
        scans = [filters for dt, filters in self.calls if dt == "CN Source Row" and "parent" in filters]
        self.assertTrue(scans)
        self.assertTrue(all(filters["parent"] == ["in", ["I"]] for filters in scans))

    def test_unclassified_legacy_deposit_is_scoped_by_import_and_reference(self):
        self.data["CN Accounting Import"].append(frappe._dict(name="I", employer="A"))
        row = frappe._dict(parent="OTHER", event_type="Deposito", reference="REF", unclassified_usd=10)
        self.data["CN Source Row"].append(row)
        with patch.object(frappe, "get_all", side_effect=self.query), \
             patch.object(frappe, "db", SimpleNamespace(exists=self.exists)):
            self.assertFalse(self.scope.has_unclassified_source_deposit({"REF"}))
            row.parent = "I"
            self.assertFalse(self.scope.has_unclassified_source_deposit({"OTHER-REF"}))
            self.assertTrue(self.scope.has_unclassified_source_deposit({"REF"}))

    def test_pending_targets_ignore_draft_cancelled_and_unrelated_parents(self):
        self.deposit("PENDING"); self.target("PENDING", period="PER")
        self.deposit("CANCELLED", status=2); self.target("CANCELLED", period="PER")
        self.deposit("OTHER"); self.target("OTHER", period="OTHER-PER")
        with patch.object(frappe, "get_all", side_effect=self.query), \
             patch.object(frappe, "db", SimpleNamespace(exists=self.exists)):
            self.assertTrue(closure.pending_registered_targets({"period": "PER"}))
            self.data["CN Remittance Target"][0].result = "Aplicada"
            self.assertFalse(closure.pending_registered_targets({"period": "PER"}))
        for dt, filters in self.calls:
            if dt == "CN Remittance Allocation":
                self.assertIn("name", filters, "Never enumerate all submitted deposits")

    def test_companies_include_payers_and_generic_pools_once_per_request(self):
        scope = closure.ClosureScope(self.period)
        with patch("credinomina_reconciliation.paying_employers.reconciliation_companies",
                   return_value=["A", "PAYER", "SHARED"]) as pool:
            self.assertEqual(scope.companies, ["A", "PAYER", "SHARED"])
            self.assertEqual(scope.companies, ["A", "PAYER", "SHARED"])
            pool.assert_called_once_with("A")

    def test_large_application_target_checks_use_bounded_batches(self):
        applications = ["APP-" + str(index) for index in range(1100)]
        with patch.object(frappe, "get_all", side_effect=self.query), \
             patch.object(frappe, "db", SimpleNamespace(exists=self.exists)):
            self.assertFalse(closure.pending_registered_targets({"historical_application": ["in", applications]}))
        queries = [filters for dt, filters in self.calls if dt == "CN Remittance Target"]
        self.assertEqual([len(query["historical_application"][1]) for query in queries], [500, 500, 100])

    def test_json_malformed_and_non_list_values_are_safe(self):
        for value in ["broken", '{"periodo":"PER"}', None, 10]:
            self.assertEqual(closure.entries(value), [])
        self.assertEqual(closure.entries('[null, 1, {"periodo":"PER"}]'), [{"periodo": "PER"}])


class CloseProgressTests(unittest.TestCase):
    def test_company_engine_receives_scope_and_progress_without_global_reconciliation(self):
        module = "credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import"
        progress = Mock()
        with patch.object(frappe, "db", SimpleNamespace(exists=lambda *_: True)), \
             patch("credinomina_reconciliation.paying_employers.reconciliation_companies", return_value=["A"]), \
             patch(module + "._reconcile_sources", return_value={"matched": 1}) as scoped, \
             patch(module + ".reconcile_all_sources") as global_reconcile:
            self.assertEqual(controller._reconcile_if_sources("A", progress), {"matched": 1})
            scoped.assert_called_once_with("A", progress=progress)
            global_reconcile.assert_not_called()

    def test_historical_close_progress_is_private_and_keeps_result(self):
        period = SimpleNamespace(name="PER", employer="A", status="Conciliado", reconciliation_mode="Historica",
            check_permission=Mock(), reload=Mock(), flags=frappe._dict(), save=Mock())
        scope = Mock(); scope.application_ids.return_value = ["APP"]
        def reconcile(*args, **kwargs):
            kwargs["progress"](50, "Depósitos")
            return {}
        with patch.object(frappe, "get_doc", return_value=period), \
             patch.object(frappe, "db", SimpleNamespace(count=lambda *_: 0)), \
             patch.object(frappe, "session", SimpleNamespace(user="operator@example.com")), \
             patch.object(frappe, "publish_realtime") as publish, \
             patch.object(controller, "_reconcile_if_sources", side_effect=reconcile), \
             patch.object(controller, "_pending_remittance_details_for_period", return_value=[]), \
             patch.object(controller, "_pending_registered_targets", return_value=False), \
             patch.object(controller, "now_datetime", return_value=datetime(2026, 10, 2)), \
             patch.object(closure, "ClosureScope", return_value=scope):
            result = controller.close_period("PER", "TOKEN")
        self.assertEqual(result["status"], "Cerrado")
        self.assertEqual(period.status_before_close, "Conciliado")
        self.assertTrue(period.flags.skip_comment_reconciliation)
        period.reload.assert_called_once(); period.save.assert_called_once()
        payloads = [call.args[1] for call in publish.call_args_list]
        self.assertEqual([payload["percent"] for payload in payloads], [5, 37, 75, 95, 100])
        for call in publish.call_args_list:
            self.assertEqual(call.args[0], "cn_period_closure_progress")
            self.assertEqual(call.kwargs["user"], "operator@example.com")
            self.assertEqual(call.args[1]["period_name"], "PER")
            self.assertEqual(call.args[1]["progress_id"], "TOKEN")

    def test_indexes_use_frappe_idempotent_api_and_target_schema_exists(self):
        with patch.object(frappe, "db", SimpleNamespace(add_index=Mock())) as db:
            indexes.execute()
            self.assertEqual(db.add_index.call_count, len(indexes.INDEXES))
            for doctype, fields, name in indexes.INDEXES:
                db.add_index.assert_any_call(doctype, fields, index_name=name)
                slug = doctype.lower().replace(" ", "_")
                schema = Path(__file__).resolve().parents[1] / "credinomina_reconciliation" / "conciliacion_credinomina" / "doctype" / slug / (slug + ".json")
                known = {field["fieldname"] for field in json.loads(schema.read_text(encoding="utf-8"))["fields"]} | {"parent"}
                self.assertTrue(set(fields) <= known)


if __name__ == "__main__":
    unittest.main()
