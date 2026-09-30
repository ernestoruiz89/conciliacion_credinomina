import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import exception_selection as selection


class ExceptionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.periods = [frappe._dict(name="P1", status="Pendiente", payroll_month="2026-09-01"),
                        frappe._dict(name="P2", status="Pendiente", payroll_month="2026-09-16")]
        self.claims = [frappe._dict(name="C1", parent="P1", client_name="Ana", client_number="12",
                                  loan_number="L1", source_row=2, expected_usd=100),
                       frappe._dict(name="C2", parent="P2", client_name="Ana", client_number="12",
                                  loan_number="L1", source_row=2, expected_usd=100)]
        self.sources = [frappe._dict(name="S1", parent="I1", source_row=8, idx=1, client_name="Ana",
                                    client_number="12", loan_number="L1", currency="NIO", amount=3653,
                                    manual_fx_rate=36.53, historical_period="P1")]
        self.queries = []
        self.permissions = set()
        self.patches = [
            patch.object(selection.frappe, "has_permission", side_effect=lambda d, p: (d, p) not in self.permissions),
            patch.object(selection.frappe, "get_doc", return_value=Mock()),
            patch.object(selection.frappe, "get_list", side_effect=self.get_list),
            patch.object(selection.frappe, "get_all", side_effect=self.get_all),
            patch.object(selection.frappe, "throw", side_effect=ValueError),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def get_list(self, doctype, **kwargs):
        self.assertEqual(kwargs["filters"]["employer"], "E1")
        return self.periods if doctype == "CN Reconciliation Period" else [frappe._dict(name="I1")]

    def get_all(self, doctype, **kwargs):
        self.queries.append((doctype, kwargs))
        filters = kwargs["filters"]
        rows = self.claims if doctype == "CN Collection Row" else self.sources
        return [r for r in rows if r.parent in filters["parent"][1]
                and (not filters.get("name") or filters["name"] == r.name)]

    def test_collection_filter_and_resolve_do_not_override_exception_amount(self):
        result = selection.get_related_cases("E1", "Cobranza", "P1", "Ana")
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["amount_usd"], 100)
        values = selection.resolve_related_case("E1", "Cobranza", "C1", "P1")
        self.assertEqual(values["collection_row_id"], "C1")
        self.assertEqual(values["source_import"], "")
        self.assertNotIn("amount_usd", values)
        doc = frappe._dict(values, amount_usd=10, description="Solo la diferencia")
        selection.validate_selected_case(doc, None)
        self.assertEqual(doc.amount_usd, 10)
        self.assertEqual(doc.description, "Solo la diferencia")

    def test_source_has_real_source_row_and_usd_conversion(self):
        result = selection.get_related_cases("E1", "Aplicación", "P1")
        self.assertEqual(result["rows"][0]["amount_usd"], 100)
        self.assertEqual(result["rows"][0]["source_row"], 8)
        values = selection.resolve_related_case("E1", "Aplicación", "S1", "P1")
        self.assertEqual(values["source_import"], "I1")
        self.assertEqual(values["collection_row_id"], "")

    def test_grouped_application_shows_both_periods_and_requires_selection(self):
        self.sources[0].historical_period = ""
        self.sources[0].application_allocation_detail = json.dumps([
            {"collection_row_id": "C1"}, {"collection_row_id": "C2"},
        ])
        result = selection.get_related_cases("E1", "Aplicación")
        self.assertEqual({r["period"] for r in result["rows"]}, {"P1", "P2"})
        with self.assertRaises(ValueError):
            selection.resolve_related_case("E1", "Aplicación", "S1")
        self.assertEqual(selection.resolve_related_case("E1", "Aplicación", "S1", "P2")["collection_row_id"], "C2")

    def test_unassigned_application_can_be_selected_without_inventing_period(self):
        self.sources[0].historical_period = ""
        values = selection.resolve_related_case("E1", "Aplicación", "S1")
        self.assertEqual(values["period"], "")

    def test_closed_inaccessible_deleted_and_wrong_period_links_rejected(self):
        self.periods[0].status = "Cerrado"
        self.assertEqual(selection.get_related_cases("E1", "Aplicación")["rows"], [])
        with self.assertRaises(ValueError):
            selection.resolve_related_case("E1", "Cobranza", "C1", "P1")
        self.periods.pop(0)
        self.assertEqual(selection.get_related_cases("E1", "Aplicación")["rows"], [])
        with self.assertRaises(ValueError):
            selection.resolve_related_case("E1", "Cobranza", "C1", "P2")
        with self.assertRaises(ValueError):
            selection.resolve_related_case("E1", "Aplicación", "DELETED")

    def test_permissions_and_parent_scope(self):
        self.permissions.add(("CN Source Import", "read"))
        self.assertEqual(selection.get_related_cases("E1", "Aplicación")["rows"], [])
        self.assertFalse(any(d == "CN Source Row" for d, _ in self.queries))
        self.permissions.update({("CN Reconciliation Exception", "create"), ("CN Reconciliation Exception", "write")})
        with self.assertRaises(ValueError):
            selection.get_related_cases("E1")

    def test_tampering_and_automatic_case_reassignment_rejected(self):
        values = selection.resolve_related_case("E1", "Cobranza", "C1", "P1")
        doc = frappe._dict(values, loan_number="OTHER")
        with self.assertRaises(ValueError):
            selection.validate_selected_case(doc, None)
        with self.assertRaises(ValueError):
            selection.validate_selected_case(frappe._dict(values, exception_key="DED-1"), None)

    def test_pagination(self):
        self.claims = [frappe._dict(self.claims[0], name=f"C{i}") for i in range(35)]
        first = selection.get_related_cases("E1")
        second = selection.get_related_cases("E1", start=30)
        self.assertEqual(len(first["rows"]), 30)
        self.assertTrue(first["has_more"])
        self.assertEqual(len(second["rows"]), 5)
        self.assertFalse(second["has_more"])

    def test_form_layout_preserves_all_fields(self):
        path = Path(__file__).resolve().parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_reconciliation_exception/cn_reconciliation_exception.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        names = [f["fieldname"] for f in meta["fields"]]
        self.assertEqual(set(names), set(meta["field_order"]))
        self.assertEqual(len(names), len(set(meta["field_order"])))
        self.assertEqual(sum(f["fieldtype"] == "Tab Break" for f in meta["fields"]), 3)


if __name__ == "__main__":
    unittest.main()
