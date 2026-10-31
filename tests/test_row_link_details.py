import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import row_link_details as api


class RowLinkDetailsTests(unittest.TestCase):
    def setUp(self):
        for replacement in [patch.object(frappe, "has_permission", return_value=True),
                            patch.object(api, "records", return_value=[frappe._dict(name="VISIBLE")])]:
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_manual_links_replace_old_suggestions_and_credit_reservations_are_separate(self):
        document = frappe._dict(name="DEP", docstatus=1, targets=[frappe._dict(
            name="T", detail_row="R", complementary_item="MANUAL", amount_usd=3, result="Pendiente")])
        row = frappe._dict(name="R", linked_usd=4, client_credit_usd=2, matched_targets=json.dumps([
            dict(claim_id="X:STALE", amount_usd=99, instruction_id="OLD"),
            dict(claim_id="X:CORE", amount_usd=1, instruction_id="RECLASS:OP")]))
        credit = frappe._dict(name="CREDIT", credit_detail_row="R", category="Saldo a favor del cliente",
                             result="Saldo a favor documentado", amount_usd=2)
        with patch("credinomina_reconciliation.client_credit.load_credits", return_value=[credit]):
            result = api._deposit(document, row)
        self.assertEqual(result["total_usd"], 6)
        self.assertEqual([entry["document"] for entry in result["rows"]], ["MANUAL", "CORE", "CREDIT"])
        self.assertEqual(result["rows"][0]["state"], "Pendiente")
        self.assertEqual(result["rows"][2]["kind"], "Saldo a favor del cliente")

    def test_fifo_displays_source_documents_and_each_exact_amount(self):
        row = frappe._dict(name="R", match_status="Conciliada", matched_targets=json.dumps([
            dict(claim_id="C:COL", amount_usd=5, fifo_applications=[
                dict(application_id="A", amount_usd=2), dict(application_id="B", amount_usd=3)])]))
        def source(doctype, name, fields, **kwargs):
            self.assertEqual(doctype, "CN Source Row")
            return frappe._dict(parent="IMPORT", parenttype="CN Accounting Import", parentfield="rows", idx=1,
                                source_row=10 if name == "A" else 11)
        with patch.object(frappe.db, "get_value", side_effect=source):
            result = api._deposit(frappe._dict(docstatus=0, targets=[]), row)
        self.assertEqual(result["total_usd"], 5)
        self.assertEqual([entry["document"] for entry in result["rows"]], ["IMPORT", "IMPORT"])
        self.assertEqual([entry["amount_usd"] for entry in result["rows"]], [2, 3])
        self.assertEqual([entry["row_label"] for entry in result["rows"]], ["Fila 10", "Fila 11"])

    def test_hidden_document_names_are_never_returned_and_corrupt_journals_are_unknown(self):
        with patch.object(api, "records", return_value=[]):
            data = api.Links()
            data.add("CN Complementary Item", "SECRET", 3, "Partida complementaria", "Confirmado")
            result = data.result()
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual(result["total_usd"], 3, "Amount belongs to the readable origin row")
        result = api._deposit(frappe._dict(docstatus=0, targets=[]), frappe._dict(name="R", matched_targets="broken"))
        self.assertIsNone(result["total_usd"])

    def application(self, deposits=(), items=(), **fields):
        row = frappe._dict(dict(name="A", parent="IMPORT", event_type="Aplicacion", historical_period="P",
                              historical_remitted_usd=30, application_adjustment_usd=2) | fields)
        with patch("credinomina_reconciliation.application_deposit_links.load_links", return_value={"A": deposits}), \
             patch("credinomina_reconciliation.application_complementary_links.load_links", return_value={"A": items}):
            return api._application(row)

    def test_application_includes_multiple_deposits_and_confirmed_adjustments_once(self):
        result = self.application(deposits=[dict(document="D1", amount_usd=10, via_collection=False),
                                           dict(document="D2", amount_usd=20, via_collection=False)],
                                  items=[dict(document="X", amount_usd=2, category="Ajuste de aplicación")])
        self.assertEqual(result["total_usd"], 32)
        self.assertEqual([row["document"] for row in result["rows"]], ["D1", "D2", "X"])

    def test_shared_or_missing_application_coverage_is_not_invented(self):
        result = self.application(deposits=[dict(document="SHARED", amount_usd=None, via_collection=True)])
        self.assertIsNone(result["total_usd"])
        self.assertIsNone(result["rows"][0]["amount_usd"])
        self.assertTrue(result["notes"])
        self.assertIsNone(self.application()["total_usd"], "Missing documents cannot turn stored coverage into zero")

    def test_real_document_shape_is_converted_before_reverse_link_lookups(self):
        class SourceDocument(SimpleNamespace):
            def get(self, field):
                return getattr(self, field, None)

            def as_dict(self):
                return self.__dict__.copy()
        row = SourceDocument(name="APP", parent="IMPORT", historical_period="P", historical_remitted_usd=10)
        deposit = frappe._dict(name="D", allocation_detail='[{"aplicacion_id":"APP","importe_usd":10}]')
        with patch("credinomina_reconciliation.application_deposit_links.records", return_value=[deposit]), \
             patch("credinomina_reconciliation.application_complementary_links.load_links", return_value={}):
            result = api._application(row)
        self.assertEqual(result["total_usd"], 10)
        self.assertEqual(result["rows"][0]["document"], "D")

    def test_endpoint_requires_origin_access_membership_and_current_version_without_writes(self):
        row = frappe._dict(name="ROW", event_type="Ajuste", complementary_item="ITEM", amount_usd=4)
        document = frappe._dict(name="IMPORT", modified="V1", rows=[row], check_permission=Mock(), save=Mock())
        with patch.object(frappe, "get_doc", return_value=document), \
             patch.object(frappe, "throw", side_effect=frappe.ValidationError), patch.object(frappe.db, "set_value") as write:
            result = api.get_links("CN Accounting Import", "IMPORT", "ROW", "V1")
            self.assertEqual(result["total_usd"], 4)
            self.assertEqual(result["modified"], "V1")
            for doctype, name, version in [("CN Accounting Import", "OTHER-ROW", "V1"),
                                           ("CN Accounting Import", "ROW", "OLD"), ("User", "ROW", "V1")]:
                with self.assertRaises(frappe.ValidationError):
                    api.get_links(doctype, "IMPORT", name, version)
            write.assert_not_called()
            document.save.assert_not_called()
            document.check_permission.assert_any_call("read")
        document.check_permission.side_effect = frappe.PermissionError
        with patch.object(frappe, "get_doc", return_value=document):
            with self.assertRaises(frappe.PermissionError):
                api.get_links("CN Accounting Import", "IMPORT", "ROW")


if __name__ == "__main__":
    unittest.main()
