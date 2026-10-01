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


if __name__ == "__main__":
    unittest.main()
