"""Guard the source-import form layout and duplicate behavior."""

import json
import unittest
from pathlib import Path


SOURCE_IMPORT_DOCTYPE = (
    Path(__file__).resolve().parents[1]
    / "credinomina_reconciliation"
    / "conciliacion_credinomina"
    / "doctype"
    / "cn_accounting_import"
    / "cn_accounting_import.json"
)


class SourceImportFormLayoutTest(unittest.TestCase):
    def test_field_order_covers_unique_fields_and_layout_breaks(self):
        doctype = json.loads(SOURCE_IMPORT_DOCTYPE.read_text(encoding="utf-8"))
        fieldnames = [field["fieldname"] for field in doctype["fields"]]
        field_order = doctype["field_order"]

        self.assertEqual(len(fieldnames), len(set(fieldnames)))
        self.assertEqual(len(field_order), len(set(field_order)))
        self.assertCountEqual(field_order, fieldnames)

    def test_form_is_grouped_and_source_file_is_not_copied(self):
        doctype = json.loads(SOURCE_IMPORT_DOCTYPE.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in doctype["fields"]}
        order = doctype["field_order"]

        self.assertEqual("Tab Break", fields["import_tab"]["fieldtype"])
        self.assertEqual("Tab Break", fields["results_tab"]["fieldtype"])
        self.assertEqual("Tab Break", fields["audit_tab"]["fieldtype"])
        self.assertEqual("Column Break", fields["source_column"]["fieldtype"])
        self.assertEqual("Column Break", fields["result_column"]["fieldtype"])
        self.assertEqual(1, fields["source_file"]["no_copy"])

        self.assertLess(order.index("import_tab"), order.index("source_file"))
        self.assertLess(order.index("results_tab"), order.index("rows"))
        self.assertLess(order.index("audit_tab"), order.index("imported_on"))

    def test_one_detail_table_and_separate_load_reconciliation_audit_steps(self):
        doctype = json.loads(SOURCE_IMPORT_DOCTYPE.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in doctype["fields"]}
        group, groups = None, {}
        for name in doctype["field_order"]:
            if fields[name]["fieldtype"] == "Tab Break":
                group = name
            groups[name] = group
        self.assertEqual([fields[name]["label"] for name in ("import_tab", "results_tab", "audit_tab")],
                         ["1. Importación", "2. Conciliación", "3. Historial"])
        self.assertEqual(doctype["field_order"][0], "import_tab")
        self.assertEqual([name for name, field in fields.items() if field["fieldtype"] == "Table"], ["rows"])
        self.assertEqual(fields["rows"]["label"], "Filas")
        for name in ("employer", "source_file", "historical_period", "portfolio_snapshot"):
            self.assertEqual(groups[name], "import_tab")
        for name in ("balance_summary_html", "rows", "notes"):
            self.assertEqual(groups[name], "results_tab")
        for name in ("row_count", "total_usd", "total_net_applied_usd", "recovery_html"):
            self.assertEqual(groups[name], "audit_tab")
        for removed in ("client_summary_html", "client_summary_section", "pending_tab", "pending_diagnostics_html"):
            self.assertNotIn(removed, fields)
        for name in ("application_amount_usd", "deposit_coverage_usd", "complementary_coverage_usd", "balance_usd"):
            self.assertEqual(fields[name]["depends_on"], "eval:false")
            self.assertFalse(fields[name].get("hidden"), "keep the list filter picker available")
            self.assertEqual(fields[name]["in_list_view"], 1)


if __name__ == "__main__":
    unittest.main()
