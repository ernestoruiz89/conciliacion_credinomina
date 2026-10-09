import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import collection_deposit_detail as api
from credinomina_reconciliation.parsers import parse_collection_file


class CollectionDepositDetailTests(unittest.TestCase):
    def document(self):
        return frappe._dict(name="DEP", doctype="CN Remittance Allocation", amount_usd=40,
            fx_rate=36.6243, modified="2026-10-09", detail_rows=[], add_comment=Mock())

    def period(self, name="P", **values):
        row = frappe._dict(doctype="CN Collection Row", name="ROW-" + name, row_key="KEY-" + name,
            client="001", client_name="Ana Prueba", loan_number="123-1", expected_usd=100,
            deducted_usd=60, applied_usd=30, remitted_usd=20)
        row.update(values)
        return frappe._dict(name=name, employer="EMP", collection_rows=[row])

    def test_uses_loaded_collection_even_without_application_and_keeps_period_identity(self):
        periods = [self.period("P1", applied_usd=0), self.period("P2", expected_usd=50)]
        result = api._preview(self.document(), periods)
        self.assertEqual(result["total_usd"], 150)
        self.assertEqual([row["deducted_usd"] for row in result["rows"]], [100, 50])
        self.assertEqual([row["period"] for row in result["rows"]], ["P1", "P2"])
        self.assertEqual(result["rows"][0]["client_number"], "001")
        self.assertEqual(result["rows"][0]["row_key"], "KEY-P1")
        self.assertIn("no confirma", result["rows"][0]["comments"])

    def test_nio_conversion_needs_rate_and_usd_takes_precedence(self):
        document = self.document()
        periods = [self.period(expected_usd=0, expected_nio=366.243)]
        self.assertEqual(api._preview(document, periods)["total_usd"], 10)
        document.fx_rate = 0
        with patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
            api._preview(document, periods)
        periods[0].collection_rows[0].expected_usd = 15
        self.assertEqual(api._preview(document, periods)["total_usd"], 15)

    def test_fingerprint_changes_with_collection_or_selected_periods(self):
        document, period = self.document(), self.period()
        before = api._preview(document, [period])["fingerprint"]
        period.collection_rows[0].expected_usd = 99
        self.assertNotEqual(api._preview(document, [period])["fingerprint"], before)
        self.assertNotEqual(api._preview(document, [self.period("OTHER")])["fingerprint"], before)
        self.assertEqual(api._preview(document, [self.period(expected_usd=0)])["rows"], [])

    def test_missing_identity_is_rejected(self):
        for values in (dict(row_key=""), dict(client_name="")):
            with self.subTest(values=values), patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
                api._preview(self.document(), [self.period(**values)])

    def test_availability_limits_child_lookup_to_readable_authorized_open_periods(self):
        with patch.object(frappe, "has_permission", return_value=True), \
             patch.object(frappe, "get_doc", return_value=Mock()), \
             patch.object(api, "allowed_employers", return_value={"EMP", "SUB"}), \
             patch.object(frappe, "get_list", return_value=["VISIBLE"]) as parents, \
             patch.object(frappe, "get_all", return_value=["ROW"]) as children:
            self.assertTrue(api.collection_detail_available("EMP", '["VISIBLE","PRIVATE"]'))
            self.assertEqual(parents.call_args.kwargs["filters"]["status"], ["!=", "Cerrado"])
            self.assertEqual(children.call_args.kwargs["filters"]["parent"], ["in", ["VISIBLE"]])
            parents.return_value = []
            children.reset_mock()
            self.assertFalse(api.collection_detail_available("EMP", '["PRIVATE"]'))
            children.assert_not_called()

    def test_generation_requires_fresh_preview_replacement_and_valid_selection(self):
        document, periods = self.document(), [self.period()]
        document.detail_file = "previous.xlsx"
        preview = api._preview(document, periods)
        with patch.object(frappe, "db", Mock()), patch.object(api, "_load", return_value=(document, periods)), \
             patch.object(frappe, "throw", side_effect=ValueError), \
             patch("frappe.utils.file_manager.save_file", return_value=frappe._dict(file_url="/private/files/new.xlsx")) as save, \
             patch("credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation._apply_remittance_detail") as apply:
            for fingerprint, replace, ids, amounts in [
                ("stale", True, None, None), (preview["fingerprint"], False, None, None),
                (preview["fingerprint"], True, ["C:FOREIGN"], None),
                (preview["fingerprint"], True, ["C:ROW-P"], {"C:ROW-P": "1.001"}),
            ]:
                with self.assertRaises(ValueError):
                    api.use_collection_detail("DEP", fingerprint, replace, ids, amounts)
            save.assert_not_called()
            result = api.use_collection_detail("DEP", preview["fingerprint"], True, ["C:ROW-P"], {"C:ROW-P": "25.95"})
        self.assertEqual(result["total_usd"], 25.95)
        self.assertTrue(save.call_args.kwargs["is_private"])
        self.assertEqual(apply.call_args.kwargs["origin"], "Cobranza de los períodos")
        self.assertEqual(periods[0].collection_rows[0].expected_usd, 100)
        parsed = parse_collection_file("detail.xlsx", save.call_args.args[1], require_deduction=True, require_name=True)
        self.assertEqual(parsed[0]["deducted_usd"], 25.95)
        self.assertIn("cobranza original US$ 100.00", parsed[0]["comments"])

    def test_button_is_next_to_applications_and_available_on_submitted_deposits(self):
        meta = json.loads((Path(__file__).parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.json").read_text(encoding="utf-8"))
        order = meta["field_order"]
        self.assertEqual(order[order.index("use_applications_detail") + 1], "use_collection_detail")
        field = next(row for row in meta["fields"] if row["fieldname"] == "use_collection_detail")
        self.assertEqual(field["label"], "Usar cobranza como detalle")
        self.assertEqual(field["allow_on_submit"], 1)
