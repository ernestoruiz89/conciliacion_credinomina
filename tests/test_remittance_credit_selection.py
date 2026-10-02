import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation import remittance_credit_selection as selection


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class RemittanceCreditSelectionTests(unittest.TestCase):
    def setUp(self):
        self.client = {"name": "3538", "client_number": "003538", "employer": "EMP",
                       "client_name": "Julio García", "national_id": "ID-1", "client_aliases": ["García Julio"]}
        self.snapshots = {"APR": {"report_date": "2025-04-30"}, "MAY": {"report_date": "2025-05-31"}}
        self.credit = {"name": "CARTERA-ROW", "parent": "APR", "credit_number": "108331-1",
                       "employer": "EMP", "matched_client": "3538", "client_number_core": "3538",
                       "national_id": "ID-1", "credit_lifecycle": "Activo"}

    def choices(self, rows):
        return selection.portfolio_credit_choices(rows, self.snapshots, self.client, "EMP")

    def test_name_match_completes_client_number_without_touching_money(self):
        row = Row(client_name="García Julio", deducted_nio=824.78, client_number="")
        selection.complete_detail_clients([row], [self.client], "EMP")
        self.assertEqual(row.client, "3538")
        self.assertEqual(row.client_number, "003538")
        self.assertEqual(row.deducted_nio, 824.78)

    def test_ambiguous_name_or_conflicting_number_is_not_filled(self):
        for row, clients in [
            (Row(client_name="Julio García"), [self.client, self.client | {"name": "other"}]),
            (Row(client_name="Julio García", client_number="OTHER", national_id="ID-1"), [self.client]),
        ]:
            original = dict(row)
            selection.complete_detail_clients([row], clients, "EMP")
            self.assertEqual({key: row[key] for key in original}, original)
            self.assertFalse(row.client)
            self.assertTrue(row.identity_reason)

    def test_canceled_credits_and_historical_cut_are_available(self):
        choices = self.choices([self.credit, self.credit | {"name": "LATER", "parent": "MAY", "credit_lifecycle": "Cancelado"}])
        self.assertEqual([item["credit_status"] for item in choices], ["Cancelado", "Activo"])
        self.assertEqual(choices[1]["report_date"], "2025-04-30")

    def test_excludes_other_clients_companies_and_unpermitted_cuts(self):
        for change in ({"matched_client": "OTHER"}, {"employer": "OTHER"},
                       {"parent": "HIDDEN"}, {"national_id": "OTHER"},
                       {"matched_client": "", "client_number_core": "OTHER"}):
            self.assertEqual(self.choices([self.credit | change]), [])

    def test_core_number_can_identify_unlinked_portfolio_row(self):
        self.assertEqual(len(self.choices([self.credit | {"matched_client": ""}])), 1)

    def test_duplicate_credit_in_same_cut_is_not_selectable(self):
        rows = [self.credit, self.credit | {"name": "DUPLICATE"}, self.credit | {"name": "THIRD"}]
        self.assertEqual(self.choices(rows), [])

    def test_save_marks_pending_and_keeps_audit_without_reconciling(self):
        try:
            import frappe
        except ImportError:
            self.skipTest("Requires Frappe runtime")
        row = Row(loan_number="")
        doc = SimpleNamespace(modified="v1", save=Mock(), flags=frappe._dict())
        with (
            patch.object(selection, "load_detail_context", return_value=(doc, row, self.client, self.choices([self.credit]))),
            patch.object(frappe, "_", side_effect=lambda text: text),
            patch.dict(frappe.__dict__, {"session": SimpleNamespace(user="operator@example.test")}),
            patch("frappe.utils.now_datetime", return_value="2026-09-29 21:20:00"),
        ):
            result = selection.set_detail_credit("REM", "DETAIL", "CARTERA-ROW", "v1")
        self.assertEqual(result["loan_number"], "108331-1")
        self.assertEqual(row.loan_selection_snapshot, "APR")
        self.assertIn("operator@example.test", row.loan_selection_note)
        self.assertEqual(row.matched_targets, "[]")
        self.assertEqual(doc.result, "Pendiente")
        doc.save.assert_called_once_with()

    def test_stale_or_forged_credit_is_rejected_without_saving(self):
        try:
            import frappe
        except ImportError:
            self.skipTest("Requires Frappe runtime")
        for credit, modified in [("CARTERA-ROW", "stale"), ("OTHER", "v1")]:
            doc = SimpleNamespace(modified="v1", save=Mock())
            with (
                patch.object(selection, "load_detail_context", return_value=(doc, Row(), self.client, self.choices([self.credit]))),
                patch.object(frappe, "_", side_effect=lambda text: text),
                patch.object(frappe, "throw", side_effect=ValueError),
                self.assertRaises(ValueError),
            ):
                selection.set_detail_credit("REM", "DETAIL", credit, modified)
            doc.save.assert_not_called()

    def test_parent_write_permission_is_checked_before_loading_client_data(self):
        try:
            import frappe
        except ImportError:
            self.skipTest("Requires Frappe runtime")
        doc = SimpleNamespace(check_permission=Mock(side_effect=PermissionError))
        with patch.object(frappe, "get_doc", return_value=doc), self.assertRaises(PermissionError):
            selection.load_detail_context("REM", "DETAIL")
        doc.check_permission.assert_called_once_with("write")
