import json
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation import application_complementary_links as links


def item(name, **values):
    return frappe._dict(dict(name=name, category="Ajuste de aplicación", posting_date="2026-02-01",
        reference="CORE", related_application="APP", related_import="IMPORT", application_adjustment_usd=1) | values)


class ApplicationComplementaryLinksTests(unittest.TestCase):
    def load(self, items, journals, hidden=()):
        sources = [dict(name="APP", parent="IMPORT"), dict(name="SECOND", parent="IMPORT2")]
        reads = []

        def query(doctype, **kwargs):
            self.assertEqual(doctype, "CN Complementary Item")
            self.assertEqual(kwargs["filters"]["docstatus"], 1)
            if kwargs["fields"] == ["name"]:
                return [frappe._dict(name=row.name) for row in items]
            names = kwargs["filters"]["name"][1]
            self.assertTrue(set(names).isdisjoint(hidden), "Never hydrate unreadable item journals")
            reads.extend(names)
            return [frappe._dict(dict(name=name) | journals.get(name, {})) for name in names]

        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(frappe, "get_all", side_effect=query), \
             patch.object(links, "records", return_value=[row for row in items if row.name not in hidden]):
            result = links.load_links(sources)
        return result, reads

    def test_direct_and_multiple_partial_adjustments_are_unique_and_exact(self):
        rows = [item("A"), item("B", related_application="SECOND", related_import="IMPORT2"),
                item("WRONG", related_application="NOT-APP"), item("ZERO", application_adjustment_usd=0),
                item("WRONG-PARENT", related_import="ELSE")]
        result, _ = self.load(rows, {"A": {"application_adjustment_history": json.dumps([
            dict(related_application="APP", related_import="IMPORT", application_adjustment_usd=.5),
            dict(related_application="SECOND", related_import="IMPORT2", application_adjustment_usd=.5)])}})
        self.assertEqual([row["document"] for row in result["APP"]], ["A"])
        self.assertEqual([row["document"] for row in result["SECOND"]], ["A", "B"])

    def test_global_core_application_portions_do_not_link_deposit_portions(self):
        rows = [item("GLOBAL", category="Compensación entre partidas")]
        entries = [dict(kind=links.APPLICATION, target="APP", parent_document="IMPORT", amount_usd=.01),
                   dict(kind=links.APPLICATION, target="SECOND", parent_document="WRONG", amount_usd=.01),
                   dict(kind="Saldo a favor contra depósito", target="SECOND", parent_document="IMPORT2", amount_usd=10)]
        result, _ = self.load(rows, {"GLOBAL": {links.FIELD: json.dumps(entries)}})
        self.assertEqual([row["document"] for row in result["APP"]], ["GLOBAL"])
        self.assertNotIn("SECOND", result)
        self.assertEqual(set(result["APP"][0]), {"document", "date", "reference"}, "Do not expose other destinations or restricted journals")

    def test_credit_funding_reversals_and_inactive_tolerances_are_excluded(self):
        rows = [item("CREDIT", category="Saldo a favor del cliente"),
                item("TOL", category="Diferencia por tolerancia", application_source_row="APP", status="Vigente"),
                item("REVERSED-TOL", category="Diferencia por tolerancia", application_source_row="APP", status="Revertido")]
        history = [dict(operation_id="OLD", application_id="APP", importe_usd=5),
                   dict(reverses="OLD", application_id="APP", importe_usd=-5),
                   dict(operation_id="NEW", application_id="SECOND", import_name="IMPORT2", importe_usd=1)]
        result, _ = self.load(rows, {"CREDIT": {"credit_history": json.dumps(history)}})
        self.assertEqual([row["document"] for row in result["APP"]], ["TOL"])
        self.assertEqual([row["document"] for row in result["SECOND"]], ["CREDIT"])

    def test_private_and_unavailable_origins_are_not_exposed(self):
        result, reads = self.load([item("PUBLIC"), item("PRIVATE")], {}, hidden={"PRIVATE"})
        self.assertEqual(reads, ["PUBLIC"])
        self.assertEqual([row["document"] for row in result["APP"]], ["PUBLIC"])
        for sources in ([], [dict(name="APP", parent="IMPORT")]):
            with patch.object(frappe, "has_permission", return_value=False), patch.object(frappe, "get_all") as query:
                self.assertEqual(links.load_links(sources), {})
                query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
