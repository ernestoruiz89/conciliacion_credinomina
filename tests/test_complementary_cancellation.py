import json
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import complementary_cancellation as cancellation
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item.cn_complementary_item import CNComplementaryItem


class ComplementaryCancellationTests(unittest.TestCase):
    def test_cancel_does_not_call_global_or_company_reconciliation(self):
        scope = {"companies": ["A"], "deposits": ["D"], "periods": ["P"], "applications": []}
        document = Mock(category="Cobranza administrativa", movement_key=None, flags=frappe._dict(cancellation_scope=scope))
        with patch.object(cancellation, "reconcile_cancellation", return_value={"deposits": ["D"]}) as run:
            CNComplementaryItem.on_cancel(document)
        run.assert_called_once_with(document, scope)
        document._reconcile.assert_not_called()
        document._reconcile_application.assert_not_called()

    def test_adjustment_cancellation_uses_same_dependency_scope(self):
        document = Mock(category="Ajuste de aplicación", movement_key=None, flags=frappe._dict(cancellation_scope={"applications": ["APP"]}))
        with patch.object(cancellation, "reconcile_cancellation") as run:
            CNComplementaryItem.on_cancel(document)
        run.assert_called_once_with(document, document.flags.cancellation_scope)
        document.db_set.assert_called_once_with("review_status", "Ajuste cancelado", update_modified=False)
        document._reconcile_application.assert_not_called()

    def test_direct_compensation_and_tolerance_do_not_reconcile_bank_cash(self):
        for category in ["Compensación entre partidas", "Diferencia por tolerancia"]:
            with patch.object(cancellation, "reconcile_cancellation") as run:
                CNComplementaryItem.on_cancel(Mock(category=category))
            run.assert_not_called()

    def test_unlinked_item_exits_without_loading_financial_documents(self):
        with patch.object(cancellation, "_engine") as engine:
            result = cancellation.reconcile_cancellation(Mock(), dict(companies=["A"], deposits=[], periods=[], applications=[]))
        self.assertEqual(result, dict(companies=[], deposits=[], periods=[], saved_imports=0))
        engine.assert_not_called()

    def test_scope_keeps_exact_actual_ids_and_all_shared_companies(self):
        item = frappe._dict(name="X1", employer="A", category="Ajuste de conciliación", generic_distribution=1,
                            distribution_companies=[frappe._dict(employer="B")])
        deposits = {name: Mock(name=name, employer=company) for name, company in [("DA", "A"), ("DB", "B")]}
        def query(doctype, filters, **kwargs):
            if doctype == "CN Remittance Target":
                return ["DA"]
            if doctype == "CN Remittance Allocation":
                return [frappe._dict(name="DB", allocation_detail='[{"partida":"X1"}]'),
                        frappe._dict(name="FALSE", allocation_detail='[{"partida":"X10"}]')]
            return []
        def get_doc(doctype, name):
            document = deposits[name]
            document.name = name
            return document
        with patch.object(cancellation, "_engine"), patch.object(cancellation, "lock_cash_pool") as lock, \
             patch.object(cancellation, "_deposit_periods", return_value=set()), \
             patch("credinomina_reconciliation.paying_employers.reconciliation_companies", side_effect=lambda name: [name]), \
             patch.object(frappe, "get_all", side_effect=query), patch.object(frappe, "get_doc", side_effect=get_doc), \
             patch.object(frappe, "db", Mock(get_value=Mock(return_value=1))):
            result = cancellation.prepare_cancellation(item)
        self.assertEqual(result["companies"], ["A", "B"])
        self.assertEqual(result["deposits"], ["DA", "DB"])
        self.assertEqual(lock.call_args.args[0], {"A", "B"})

    def test_related_cash_is_read_only_and_uses_exact_claim_links(self):
        unrelated = frappe._dict(name="OTHER", allocation_detail='[{"periodo":"P10"}]')
        related = frappe._dict(name="SHARED", allocation_detail='[{"aplicacion_id":"APP"}]')
        with patch.object(frappe, "get_all", return_value=[unrelated, related, frappe._dict(name="D", allocation_detail='[{"periodo":"P1"}]')]):
            result = cancellation._related_cash([frappe._dict(name="D")], [frappe._dict(name="P1")],
                                                [frappe._dict(name="APP", event_type="Aplicacion")], [])
        self.assertEqual([row.name for row in result], ["SHARED"])

    def test_deposit_periods_include_selected_actual_and_manual_links(self):
        deposit = frappe._dict(detail_periods=[frappe._dict(period="SELECTED")],
            allocation_detail=json.dumps([{"periodo": "ACTUAL"}, {"aplicacion_id": "H2"}, {"partida": "X"}]),
            targets=[frappe._dict(period="MANUAL", historical_application="H1")])
        with patch.object(cancellation, "_application_periods", return_value={"APP"}) as resolve, \
             patch.object(frappe, "get_all", return_value=["FEE"]):
            self.assertEqual(cancellation._deposit_periods([deposit]), {"SELECTED", "ACTUAL", "MANUAL", "APP", "FEE"})
        resolve.assert_called_once_with({"H1", "H2"})

    def test_claim_evidence_excludes_other_company_complements(self):
        items = [frappe._dict(name=name, period=period, reference=reference, generic_distribution=generic)
                 for name, period, reference, generic in [("P", "PER", "", 0), ("LINK", "", "", 1),
                    ("REF", "", "BANK", 0), ("MAP", "", "", 0), ("OTHER", "OLD", "OLD-BANK", 0),
                    ("GENERIC-UNUSED", "", "BANK", 1)]]
        periods = [frappe._dict(name="PER", collection_rows=[frappe._dict(name="C")])]
        deposits = [frappe._dict(deposit_reference="BANK", targets=[frappe._dict(complementary_item="LINK")])]
        self.assertEqual([item.name for item in cancellation._scoped_items(items, periods, deposits,
                         {("C", "REF"): [items[3]]})], ["P", "LINK", "REF", "MAP"])

    def test_closed_affected_period_blocks_preparation(self):
        item = frappe._dict(name="X", employer="A", category="Ajuste de conciliación", period="CLOSED")
        def query(doctype, **kwargs):
            if doctype == "CN Reconciliation Period":
                return ["CLOSED"] if "status" in kwargs["filters"] else ["A"]
            return []
        with patch.object(cancellation, "_engine"), patch.object(cancellation, "lock_cash_pool"), \
             patch("credinomina_reconciliation.paying_employers.reconciliation_companies", return_value=["A"]), \
             patch.object(frappe, "get_all", side_effect=query), patch.object(frappe, "throw", side_effect=lambda message: (_ for _ in ()).throw(ValueError(message))), \
             patch.object(cancellation, "_", side_effect=lambda value: value):
            with self.assertRaisesRegex(ValueError, "CLOSED"):
                cancellation.prepare_cancellation(item)

    def test_import_lookup_does_not_load_substring_only_matches(self):
        # Exact IDs must be checked before loading a parent or auditing its
        # company: an unrelated similar name cannot block this cancellation.
        def query(doctype, **kwargs):
            if "or_filters" in kwargs:
                return [frappe._dict(parent="OTHER", application_allocation_detail='[{"period":"P10","collection_row_id":"C10"}]')]
            return []
        with patch.object(frappe, "get_all", side_effect=query), patch.object(frappe, "get_doc") as load, \
             patch.object(cancellation, "_engine"):
            self.assertEqual(cancellation._load_imports([frappe._dict(name="P1", collection_rows=[frappe._dict(name="C1")])],
                                                       [], [], {"A"}), [])
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
