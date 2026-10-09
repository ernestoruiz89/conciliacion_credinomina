import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import accounting_review as api
from credinomina_reconciliation import application_adjustments as adjustments


class Row(frappe._dict):
    def as_dict(self):
        return dict(self)


class ApplicationCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.item = frappe._dict(name="ITEM", employer="EMP", docstatus=0, amount_usd=-30,
            category="Por clasificar", check_permission=Mock())
        self.parent = frappe._dict(name="IMPORT", employer="EMP", docstatus=0, status="Importado",
            check_permission=Mock(), rows=[])
        self.confirmed = []
        self.db = Mock(exists=Mock(return_value=False), get_value=Mock(return_value=None))

    def row(self, name="APP", **values):
        row = Row(name=name, parent="IMPORT", parenttype="CN Accounting Import", event_type="Aplicacion",
            effective=1, currency="USD", amount=100, amount_usd=100,
            application_adjustment_usd=0, net_applied_usd=100, historical_remitted_usd=0,
            historical_period="P", application_allocation_detail="[]")
        row.update(values)
        self.parent.rows.append(row)
        return row

    def candidates(self, coverage=None):
        def cash(row):
            if coverage:
                return coverage(row)
            return dict(adjustable_usd=row.net_applied_usd, protected_usd=0, snapshots={"private": "evidence"})
        with patch.object(api.frappe, "get_doc", side_effect=[self.item, self.parent]), \
             patch.object(api.frappe, "get_all", return_value=self.confirmed), \
             patch.object(api.frappe, "db", self.db), \
             patch.object(adjustments, "cash_coverage", side_effect=cash):
            return api.application_candidates(self.item.name, self.parent.name)

    def test_excludes_fully_paid_but_keeps_partially_adjustable_applications(self):
        self.row("PAID", historical_remitted_usd=100)
        self.row("PARTIAL", historical_remitted_usd=80)
        result = self.candidates(lambda row: dict(adjustable_usd=row.net_applied_usd-row.historical_remitted_usd,
            protected_usd=row.historical_remitted_usd, snapshots={}))
        self.assertEqual([row["name"] for row in result], ["PARTIAL"])
        self.assertEqual(result[0]["adjustable_usd"], 20)
        self.assertNotIn("snapshots", result[0])

    def test_excludes_wrong_type_inactive_currency_parent_and_period(self):
        self.item.period = "P"
        for i, values in enumerate([dict(event_type="Deposito"), dict(effective=0), dict(currency="NIO"),
                dict(parenttype="Other"), dict(historical_period="OTHER")]):
            self.row(str(i), **values)
        self.row("VALID")
        self.assertEqual([row["name"] for row in self.candidates()], ["VALID"])

    def test_failed_draft_and_cancelled_imports_have_no_candidates(self):
        self.row()
        for status, docstatus in [("Borrador", 0), ("Fallido", 0), ("Importado", 2)]:
            self.parent.status, self.parent.docstatus = status, docstatus
            self.assertEqual(self.candidates(), [])

    def test_closed_periods_are_excluded_including_secondary_collection_links(self):
        self.row("CLOSED", historical_period="CLOSED")
        self.row("MULTI", historical_period=None, application_allocation_detail='[{"collection_row_id":"COL"}]')
        self.row("OPEN")
        def get_value(doctype, name, field, **kwargs):
            if doctype == "CN Collection Row":
                return "CLOSED"
            return "Cerrado" if name == "CLOSED" else "Pendiente"
        self.db.get_value.side_effect = get_value
        self.assertEqual([row["name"] for row in self.candidates()], ["OPEN"])

    def test_item_reserved_as_deposit_target_cannot_link(self):
        self.row()
        self.db.exists.return_value = True
        self.assertEqual(self.candidates(), [])

    def test_confirmed_adjustments_override_stale_import_totals_without_writing(self):
        paid = self.row("ADJUSTED")
        partial = self.row("PARTIAL")
        self.confirmed = [frappe._dict(related_application="ADJUSTED", application_adjustment_usd=100),
                          frappe._dict(related_application="PARTIAL", application_adjustment_usd=40)]
        result = self.candidates()
        self.assertEqual([row["name"] for row in result], ["PARTIAL"])
        self.assertEqual((result[0]["application_adjustment_usd"], result[0]["net_applied_usd"]), (40, 60))
        self.assertEqual((paid.application_adjustment_usd, partial.application_adjustment_usd), (0, 0))
        self.db.set_value.assert_not_called()

    def test_noneditable_items_and_zero_amount_have_no_candidates(self):
        self.row()
        for values in [dict(docstatus=1), dict(docstatus=2), dict(amount_usd=0),
                       dict(category="Compensación entre partidas"), dict(category="Saldo a favor del cliente")]:
            original = dict(self.item)
            self.item.update(values)
            self.assertEqual(self.candidates(), [])
            self.item.update(original)

    def test_permissions_are_checked_before_returning_candidates(self):
        self.row()
        self.candidates()
        self.item.check_permission.assert_called_once_with("write")
        self.parent.check_permission.assert_called_once_with("read")
        self.item.check_permission.side_effect = frappe.PermissionError
        with self.assertRaises(frappe.PermissionError):
            self.candidates()
