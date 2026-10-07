import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.remittance_portfolio import apply_credit_states, load_cuts, update_detail_portfolio


class RemittancePortfolioTests(unittest.TestCase):
    def setUp(self):
        self.cuts = [frappe._dict(name="NEW", report_date="2025-06-30"),
                     frappe._dict(name="PREVIOUS", report_date="2025-05-31")]

    def state(self, portfolio, **detail):
        row = frappe._dict(loan_number="123", employer="A", **detail)
        apply_credit_states([row], self.cuts, [frappe._dict(r) for r in portfolio], {"A"})
        return row

    def entry(self, cut="NEW", **values):
        return dict(parent=cut, credit_number="123-1", employer="A", credit_status="VIGENTE", **values)

    def test_latest_state_wins_and_date_is_actual_source(self):
        row = self.state([self.entry(), self.entry("PREVIOUS")])
        self.assertEqual((row.portfolio_credit_status, row.portfolio_report_date, row.portfolio_snapshot_used),
                         ("VIGENTE", "2025-06-30", "NEW"))

    def test_missing_credit_uses_only_previous_cut(self):
        row = self.state([self.entry("PREVIOUS")])
        self.assertEqual(row.portfolio_report_date, "2025-05-31")
        self.assertEqual(row.portfolio_snapshot_used, "PREVIOUS")

    def test_not_found_clears_stale_evidence_and_never_uses_older_cut(self):
        row = self.state([self.entry("OLDER")], portfolio_credit_status="OLD", portfolio_report_date="2000-01-01")
        self.assertEqual(row.portfolio_credit_status, "No Identificado")
        self.assertIsNone(row.portfolio_report_date)
        self.assertIsNone(row.portfolio_snapshot_used)

    def test_ambiguity_and_client_conflict_do_not_use_previous_state(self):
        for entries in ([self.entry(), self.entry(), self.entry("PREVIOUS")],
                        [self.entry(matched_client="OTHER"), self.entry("PREVIOUS")]):
            self.assertEqual(self.state(entries, client="CLIENT").portfolio_credit_status, "No Identificado")

    def test_other_company_does_not_supply_state(self):
        entry = self.entry()
        entry["employer"] = "B"
        self.assertEqual(self.state([entry]).portfolio_credit_status, "No Identificado")

    @patch("frappe.has_permission", return_value=True)
    @patch("frappe.get_all")
    @patch("frappe.get_list")
    def test_latest_selected_period_then_immediate_previous_unselected_cut(self, get_list, get_all, _permission):
        get_all.return_value = [frappe._dict(parent="IMPORT", portfolio_snapshot_used="AUTOMATIC")]
        get_list.side_effect = [
            [frappe._dict(name="EARLY", employer="A", historical_application_date="2025-05-30"),
             frappe._dict(name="LATE", employer="A", historical_application_date="2025-06-15")],
            [frappe._dict(name="IMPORT", portfolio_snapshot="EXPLICIT")],
            self.cuts[:1], self.cuts[1:],
        ]
        doc = frappe._dict(detail_periods=[{"period": "EARLY"}, {"period": "LATE"}])
        self.assertEqual(load_cuts(doc, {"A"}), self.cuts)
        self.assertEqual(get_all.call_args.kwargs["or_filters"]["historical_period"], "LATE")
        self.assertEqual(get_list.call_args_list[2].kwargs["filters"]["name"], ["in", ["EXPLICIT"]])
        self.assertEqual(get_list.call_args_list[3].kwargs["filters"]["report_date"], ["<", "2025-06-30"])

    @patch("frappe.has_permission", return_value=True)
    @patch("frappe.get_all", return_value=[frappe._dict(parent="IMPORT", portfolio_snapshot_used="AUTOMATIC")])
    @patch("frappe.get_list")
    def test_automatic_snapshot_recorded_on_source_is_used(self, get_list, _rows, _permission):
        get_list.side_effect = [[frappe._dict(name="P", employer="A", payroll_month="2025-06-01")],
            [frappe._dict(name="IMPORT", portfolio_snapshot=None)], self.cuts[:1], []]
        load_cuts(frappe._dict(detail_periods=[{"period": "P"}]), {"A"})
        self.assertEqual(get_list.call_args_list[2].kwargs["filters"]["name"], ["in", ["AUTOMATIC"]])

    @patch("credinomina_reconciliation.remittance_portfolio.load_cuts")
    def test_ordinary_save_preserves_server_values_without_loading_portfolio(self, load):
        old = frappe._dict(name="ROW", loan_number="123", portfolio_credit_status="VIGENTE")
        previous = frappe._dict(employer="A", detail_periods=[], detail_rows=[old])
        current = frappe._dict(previous, detail_rows=[frappe._dict(old, portfolio_credit_status="EDITED")])
        current.get_doc_before_save = lambda: previous
        update_detail_portfolio(current)
        self.assertEqual(current.detail_rows[0].portfolio_credit_status, "VIGENTE")
        load.assert_not_called()

    @patch("frappe.get_list")
    def test_without_selected_periods_no_global_latest_is_assumed(self, query):
        self.assertEqual(load_cuts(frappe._dict(detail_periods=[]), {"A"}), [])
        query.assert_not_called()
