import json
import unittest
from pathlib import Path


class RemittanceFormLayoutTests(unittest.TestCase):
    def test_fields_are_preserved_and_grouped_into_workflow_tabs(self):
        path = Path(__file__).resolve().parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in doc["fields"]}
        self.assertEqual(set(fields), set(doc["field_order"]))
        self.assertEqual(len(fields), len(doc["field_order"]))
        groups = {}
        tab = None
        for name in doc["field_order"]:
            if fields[name]["fieldtype"] == "Tab Break":
                tab = name
            groups[name] = tab
        for name in ("employer", "deposit_amount", "fx_rate", "amount_usd", "support_file", "notes"):
            self.assertEqual(groups[name], "deposit_tab")
        for name in ("detail_file", "detail_periods", "load_deposit_detail", "detail_rows"):
            self.assertEqual(groups[name], "detail_tab")
        self.assertEqual(groups["targets"], "destinations_tab")
        self.assertEqual(groups["complete_distribution"], "destinations_tab")
        self.assertEqual(fields["complete_distribution"]["fieldtype"], "HTML")
        self.assertTrue(fields["refresh_distribution"]["allow_on_submit"])
        self.assertEqual(groups["allocation_preview"], "results_tab")
        self.assertEqual(groups["allocation_detail"], "results_tab")
        target_index = doc["field_order"].index("targets")
        self.assertEqual(doc["field_order"][target_index - 2:target_index],
                         ["select_pending_targets", "link_detail_targets"])
        self.assertTrue(fields["technical_section"]["collapsible"])
        self.assertGreater(doc["field_order"].index("unallocated_usd"), doc["field_order"].index("technical_section"))
        self.assertIn("Incluye los saldos a favor", fields["unallocated_usd"]["description"])
        self.assertEqual(fields["unclassified_usd"]["label"], "Sin asignar ni justificar US$")
        self.assertTrue(fields["result"]["allow_on_submit"])
        self.assertFalse(fields["detail_rows"].get("read_only"))
        self.assertTrue(fields["detail_rows"]["allow_on_submit"])
        child_path = path.parent.parent / "cn_remittance_detail/cn_remittance_detail.json"
        child_fields = json.loads(child_path.read_text(encoding="utf-8"))["fields"]
        editable = [field["fieldname"] for field in child_fields
                    if not field.get("read_only") and field["fieldtype"] not in ("Section Break", "Button")]
        self.assertEqual(editable, ["employer", "loan_number"])
        self.assertTrue(next(field for field in child_fields if field["fieldname"] == "loan_number")["allow_on_submit"])
        button = next(field for field in child_fields if field["fieldname"] == "create_client_credit")
        self.assertEqual(button["fieldtype"], "Button")
        self.assertTrue(button["allow_on_submit"])
        self.assertNotIn("in_list_view", button)  # Preserve the existing ten-column grid.
