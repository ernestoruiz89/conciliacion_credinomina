import unittest
from unittest.mock import patch

import frappe
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import control_credinomina as control


class ControlYearFilterTests(unittest.TestCase):
    def load(self, year):
        queries = []
        def get_list(doctype, **kwargs):
            queries.append((doctype, kwargs))
            if doctype == "CN Accounting Import":
                return ["IMP"] if kwargs.get("pluck") else [frappe._dict(name="IMP")]
            return []
        def get_all(doctype, **kwargs):
            queries.append((doctype, kwargs))
            return []
        with patch.object(control.frappe, "has_permission", return_value=True), \
             patch.object(control.frappe, "get_list", side_effect=get_list), \
             patch.object(control.frappe, "get_all", side_effect=get_all):
            data = control.get_control_data(year)
        return data, queries

    def test_all_years_removes_every_date_restriction_and_screen_limit(self):
        data, queries = self.load("Todos")
        self.assertEqual(data["year"], "Todos")
        for dt, query in queries:
            filters = query.get("filters", {})
            for field in ("payroll_month", "event_date", "deposit_date"):
                self.assertNotIn(field, filters, (dt, filters))
            if "limit_page_length" in query:
                # Receivables use the permission-aware paginator, not a screen cap.
                if dt == 'CN Complementary Item' and 'subcategory_effect' in filters:
                    self.assertEqual(query['limit_page_length'], 500)
                    self.assertEqual(query.get('limit_start'), 0)
                else:
                    self.assertEqual(query["limit_page_length"], 0)

    def test_specific_year_keeps_date_filters(self):
        data, queries = self.load(2025)
        self.assertEqual(data["year"], 2025)
        for dt, field in (("CN Reconciliation Period", "payroll_month"), ("CN Remittance Allocation", "deposit_date"), ("CN Source Row", "event_date")):
            matches = [q["filters"][field] for d, q in queries if d == dt and field in q.get("filters", {})]
            self.assertTrue(matches, dt)
            self.assertTrue(all(value == ["between", ["2025-01-01", "2025-12-31"]] for value in matches))

    def test_period_query_includes_remark_for_month_cards(self):
        _, queries = self.load(2025)
        query = next(q for dt, q in queries if dt == "CN Reconciliation Period"
                     and "name" in q.get("fields", []))
        self.assertIn("remark", query["fields"])

    def test_year_catalog_uses_visible_parents_and_selected_employer(self):
        def get_list(dt, **kwargs):
            self.assertEqual(kwargs["filters"]["employer"], "EMP")
            if dt == "CN Reconciliation Period":
                return [frappe._dict(payroll_month="2010-01-01")]
            if dt == "CN Remittance Allocation":
                return [frappe._dict(deposit_date="2027-01-01")]
            return ["VISIBLE-IMPORT"]
        with patch.object(control.frappe, "has_permission", return_value=True), \
             patch.object(control.frappe, "get_list", side_effect=get_list), \
             patch.object(control.frappe, "get_all", return_value=[frappe._dict(event_date="2026-03-01")]) as query:
            self.assertEqual(control._available_years("EMP"), [2027, 2026, 2010])
        self.assertEqual(query.call_args.kwargs["filters"]["parent"], ["in", ["VISIBLE-IMPORT"]])

    def test_catalog_respects_doctype_read_permissions(self):
        with patch.object(control.frappe, "has_permission", return_value=False), \
             patch.object(control.frappe, "get_list") as query:
            self.assertEqual(control._available_years(), [])
        query.assert_not_called()
