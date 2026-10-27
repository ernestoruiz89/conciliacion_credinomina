import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from credinomina_reconciliation import difference_documentation as module
from credinomina_reconciliation.manual_items_review import review_rows, summarize_rows, execute, ITEM, DEPOSIT, IMPORT, PERIOD
from credinomina_reconciliation.manual_complementary_policy import guard_new_item, imported_item
from credinomina_reconciliation.rounding import rounding_movements


class DifferenceDocumentationTests(unittest.TestCase):
    def test_target_requires_exact_parent_and_child(self):
        row = frappe._dict(name="R", doctype="CN Source Row")
        doc = frappe._dict(doctype=IMPORT, rows=[row])
        self.assertIs(module.target(doc, "rows", "R"), row)
        with patch.object(module.frappe, "throw", side_effect=ValueError):
            for field, name in (("targets", "R"), ("rows", "OTHER"), ("", "")):
                with self.assertRaises(ValueError):
                    module.target(doc, field, name)

    def test_new_manual_and_old_draft_submission_are_blocked(self):
        for new, status, previous_status, key, blocked in (
            (True, 0, None, "", True), (False, 1, 0, "", True),
            (False, 1, 1, "", False), (True, 0, None, "CORE", True),
        ):
            doc = SimpleNamespace(is_new=lambda: new, docstatus=status,
                get=lambda name: key, get_doc_before_save=lambda: None if previous_status is None else SimpleNamespace(docstatus=previous_status))
            with patch.object(frappe, "throw", side_effect=ValueError):
                if blocked:
                    with self.assertRaises(ValueError): guard_new_item(doc)
                else:
                    guard_new_item(doc)

    def test_import_service_can_create_core_evidence_but_context_cannot_escape(self):
        doc = SimpleNamespace(is_new=lambda: True, docstatus=0, get=lambda name: "KEY",
                              get_doc_before_save=lambda: None)
        with patch.object(frappe, "throw", side_effect=ValueError):
            with imported_item("KEY"):
                guard_new_item(doc)
            with self.assertRaises(ValueError):
                guard_new_item(doc)

    def test_even_authorized_positive_and_negative_tolerances_do_not_absorb_cash(self):
        for paid, applied in ((46.53, 46.52), (46.52, 46.53)):
            args = ([{"id": "D", "amount_usd": paid}], [{"id": "H:A", "amount_usd": applied}],
                    [{"deposit_id": "D", "claim_id": "H:A", "amount_usd": min(paid, applied)}],
                    {"D": max(paid-applied, 0)}, {"H:A": max(applied-paid, 0)}, {"E": .10})
            before = copy.deepcopy(args)
            self.assertEqual(rounding_movements(*args), [])
            self.assertEqual(before, args)


class ManualReviewTests(unittest.TestCase):
    def evidence(self):
        nodes = {
            (ITEM, "M"): dict(name="M", employer="E", amount_usd=10, related_application="A", registered_deposit="D", period="P", voucher="V"),
            (DEPOSIT, "D"): dict(name="D", employer="E", docstatus=1, allocation_detail='[{"partida":"M","importe_usd":10}]'),
            (IMPORT, "I"): dict(name="I", employer="E", source_file="core.xlsx"),
            ("CN Source Row", "A"): dict(name="A", parent="I", historical_period="P", accounting_source_key="K", voucher="V"),
            (PERIOD, "P"): dict(name="P", status="Cerrado"),
            ("CN Deposit Accounting Source", "S"): dict(name="S", parent="D", accounting_source_key="KD", source_file="deposit.xlsx"),
        }
        owners = {key: key for key in nodes}
        owners[("CN Source Row", "A")] = (IMPORT, "I")
        owners[("CN Deposit Accounting Source", "S")] = (DEPOSIT, "D")
        metadata = {kind: SimpleNamespace(fields=[]) for kind, name in nodes}
        metadata[ITEM].fields = [SimpleNamespace(fieldname=field, fieldtype="Link", options=kind) for field, kind in (("registered_deposit", DEPOSIT), ("period", PERIOD))]
        metadata["CN Source Row"].fields = [SimpleNamespace(fieldname="historical_period", fieldtype="Link", options=PERIOD)]
        return {key: frappe._dict(value) for key, value in nodes.items()}, owners, metadata

    def test_report_traces_core_deposit_closed_period_and_keeps_candidate_separate(self):
        nodes, owners, metadata = self.evidence()
        before = copy.deepcopy(nodes)
        rows = review_rows(nodes, owners, metadata)
        self.assertTrue(any(row.get("related_document") == "I" and row.get("related_row") == "A" for row in rows))
        self.assertTrue(any(row.get("source_key") == "KD" for row in rows))
        self.assertTrue(any(row.get("related_document") == "P" and row.get("related_status") == "Cerrado" for row in rows))
        self.assertTrue(any(row.get("relation_usd") == 10 and row.get("related_document") == "D" for row in rows))
        self.assertTrue(any(row.get("evidence") == "Candidato; requiere revisión" for row in rows))
        self.assertEqual(nodes, before)

    def test_malformed_journal_and_restricted_link_never_look_like_no_issues(self):
        nodes, owners, metadata = self.evidence()
        nodes[(ITEM, "M")].credit_history = "broken"
        nodes[(ITEM, "M")].related_application = "RESTRICTED"
        rows = review_rows(nodes, owners, metadata)
        self.assertTrue(all("Historial inválido" in row["observation"] for row in rows))
        self.assertTrue(all("restringido" in row["observation"] for row in rows))
        self.assertNotIn("RESTRICTED", str(rows))

    def test_tolerance_filter(self):
        nodes, owners, metadata = self.evidence()
        nodes[(ITEM, "M")].category = "Diferencia por tolerancia"
        self.assertEqual(review_rows(nodes, owners, metadata), [])
        self.assertTrue(review_rows(nodes, owners, metadata, {"include_tolerance": 1}))
        self.assertEqual(review_rows(nodes, owners, metadata, {"include_tolerance": "0"}), [])

    def test_reverse_compensation_points_to_core_parent_with_its_journal_amount(self):
        nodes, owners, metadata = self.evidence()
        core, offset = (ITEM, "CORE"), ("CN Complementary Offset", "OFFSET")
        nodes[core] = frappe._dict(name="CORE", accounting_source_key="KCORE", employer="E")
        owners[core] = core
        nodes[offset] = frappe._dict(name="OFFSET", parent="CORE", counterpart="M", amount_usd=4)
        owners[offset] = core
        metadata[offset[0]] = SimpleNamespace(fields=[])
        rows = review_rows(nodes, owners, metadata)
        self.assertTrue(any(row.get("related_document") == "CORE" and row.get("related_row") == "OFFSET"
                            and row.get("relation_usd") == 4 and row.get("source_key") == "KCORE" for row in rows))

    def test_adjustment_traces_application_to_operative_period_and_its_cash(self):
        nodes, owners, metadata = self.evidence()
        nodes[(ITEM, "M")].registered_deposit = nodes[(ITEM, "M")].period = ""
        nodes[("CN Source Row", "A")].historical_period = ""
        nodes[("CN Source Row", "A")].application_allocation_detail = '[{"collection_row_id":"C","amount_usd":10}]'
        nodes[(DEPOSIT, "D")].allocation_detail = '[{"aplicacion_id":"A","importe_usd":10}]'
        key = ("CN Collection Row", "C")
        nodes[key] = frappe._dict(name="C", parent="P")
        owners[key] = (PERIOD, "P")
        metadata[key[0]] = SimpleNamespace(fields=[])
        rows = review_rows(nodes, owners, metadata)
        self.assertTrue(any(row.get("related_document") == "P" and row.get("related_row") == "C" for row in rows))
        self.assertTrue(any(row.get("related_document") == "D" for row in rows))
        self.assertTrue(any(row.get("source_key") == "KD" for row in rows))

    def test_shared_period_import_and_deposit_do_not_expand_to_unrelated_applications(self):
        nodes, owners, metadata = self.evidence()
        nodes[(ITEM, "M")].related_import = "I"
        for field, kind in (("related_import", IMPORT), ("remittance_allocation", DEPOSIT)):
            metadata["CN Source Row"].fields.append(SimpleNamespace(fieldname=field, fieldtype="Link", options=kind))
        for index in range(1000):
            key = ("CN Source Row", f"UNRELATED-{index}")
            nodes[key] = frappe._dict(name=key[1], parent="I", historical_period="P",
                                     related_import="I", remittance_allocation="D")
            owners[key] = (IMPORT, "I")
        rows = review_rows(nodes, owners, metadata)
        self.assertNotIn("UNRELATED-", str(rows))
        self.assertTrue(any(row.get("related_row") == "A" for row in rows))
        self.assertTrue(any(row.get("related_document") == "P" for row in rows))
        self.assertTrue(any(row.get("source_key") == "KD" for row in rows))

    def test_summary_keeps_one_balance_per_item_and_counts_distinct_targets(self):
        nodes, owners, metadata = self.evidence()
        nodes[(ITEM, "M")].update(applied_usd=3, pending_usd=7)
        key = (ITEM, "UNLINKED")
        nodes[key] = frappe._dict(name=key[1], amount_usd=5, reconciliation_status="Saldo no disponible")
        owners[key] = key
        detail = review_rows(nodes, owners, metadata)
        summary = summarize_rows(detail + detail)
        self.assertEqual(len(summary), 2)
        manual = next(row for row in summary if row["item"] == "M")
        self.assertEqual((manual["amount_usd"], manual["applied_usd"], manual["pending_usd"]), (10, 3, 7))
        self.assertEqual(manual["link_count"], 4)  # application, deposit, period, core source
        self.assertEqual(manual["candidate_count"], 0)  # same application already linked
        unlinked = next(row for row in summary if row["item"] == "UNLINKED")
        self.assertEqual(unlinked["link_count"], 0)
        self.assertIsNone(unlinked["pending_usd"])

    def test_report_defaults_to_summary_and_keeps_detail_explicit(self):
        with patch("credinomina_reconciliation.manual_items_review.load_evidence", return_value=self.evidence()), \
                patch.object(frappe, "has_permission", return_value=True):
            columns, rows, message = execute({"show_details": "0"})
            self.assertEqual(len(rows), 1)
            self.assertIn("details", [column["fieldname"] for column in columns])
            self.assertNotIn("relation_usd", [column["fieldname"] for column in columns])
            columns, rows, message = execute({"item": "M", "show_details": "1"})
            self.assertGreater(len(rows), 1)
            self.assertIn("relation_usd", [column["fieldname"] for column in columns])


if __name__ == "__main__":
    unittest.main()
