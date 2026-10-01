"""Keep the reconciliation-period form compact and workflow-oriented."""

import json
import unittest
from pathlib import Path


PERIOD_DOCTYPE = (
    Path(__file__).resolve().parents[1]
    / "credinomina_reconciliation"
    / "conciliacion_credinomina"
    / "doctype"
    / "cn_reconciliation_period"
    / "cn_reconciliation_period.json"
)


class PeriodFormLayoutTest(unittest.TestCase):
    def test_field_order_covers_unique_fields_and_layout_breaks(self):
        doctype = json.loads(PERIOD_DOCTYPE.read_text(encoding="utf-8"))
        fieldnames = [field["fieldname"] for field in doctype["fields"]]
        field_order = doctype["field_order"]

        self.assertEqual(len(fieldnames), len(set(fieldnames)))
        self.assertEqual(len(field_order), len(set(field_order)))
        self.assertCountEqual(field_order, fieldnames)

    def test_period_workflow_is_split_into_tabs_and_columns(self):
        doctype = json.loads(PERIOD_DOCTYPE.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in doctype["fields"]}
        order = doctype["field_order"]

        self.assertEqual("Tab Break", fields["main_tab"]["fieldtype"])
        self.assertEqual("Tab Break", fields["detail_tab"]["fieldtype"])
        self.assertEqual("Tab Break", fields["tracking_tab"]["fieldtype"])
        self.assertEqual("Column Break", fields["period_column"]["fieldtype"])
        self.assertEqual("Column Break", fields["summary_column"]["fieldtype"])

        self.assertLess(order.index("main_tab"), order.index("collection_file"))
        self.assertLess(order.index("main_tab"), order.index("collection_rows"))
        self.assertLess(order.index("detail_tab"), order.index("collection_rows"))
        self.assertLess(order.index("tracking_tab"), order.index("control_cut_on"))
        self.assertEqual(
            "eval:doc.reconciliation_mode == 'Historica'",
            fields["historical_section"]["depends_on"],
        )

    def test_exceptions_are_visible_in_main_tab_in_both_modes(self):
        doctype = json.loads(PERIOD_DOCTYPE.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in doctype["fields"]}
        order = doctype["field_order"]
        self.assertEqual("HTML", fields["exceptions_html"]["fieldtype"])
        self.assertEqual("eval:!doc.__islocal", fields["exceptions_section"]["depends_on"])
        self.assertLess(order.index("exception_count"), order.index("exceptions_section"))
        self.assertLess(order.index("exceptions_html"), order.index("detail_tab"))
        self.assertEqual("Excepciones abiertas", fields["exception_count"]["label"])


if __name__ == "__main__":
    unittest.main()
