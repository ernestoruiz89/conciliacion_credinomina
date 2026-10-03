import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

import frappe

from credinomina_reconciliation import control_kpis as kpis
from credinomina_reconciliation.application_aging import application_balances
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import control_credinomina as page


class ControlKpiTests(unittest.TestCase):
    def figures(self, rows, collections=None, year=2025, employer=None, today="2025-05-11"):
        periods = {"H": {"name": "H", "employer": "E", "payroll_month": "2025-04-01"},
                   "P": {"name": "P", "employer": "E", "payroll_month": "2025-04-01"}}
        imports = {"I": {"name": "I", "employer": "E"}}
        balances = application_balances(rows, imports, periods, collections or {},
                                        {"E": {"grace_days": 10}}, today, include_settled=True)
        return kpis.summarize_applications(balances, year, employer)

    def source(self, **changes):
        return dict({"name": "R", "parent": "I", "event_type": "Aplicacion", "effective": 1,
                     "currency": "USD", "amount": 100, "event_date": "2025-04-30",
                     "historical_period": "H", "match_status": "Conciliado"}, **changes)

    def test_net_total_includes_settled_unlinked_and_partial_adjustments(self):
        result = self.figures([
            self.source(historical_remitted_usd=100),
            self.source(name="UNLINKED", historical_period="", match_status="Sin coincidencia", amount=20),
            self.source(name="PARTIAL", amount=100, application_adjustment_usd=10, historical_remitted_usd=60),
            self.source(name="REVERSED", amount=10, application_adjustment_usd=10),
            self.source(name="IGNORED", effective=0),
        ])
        self.assertEqual((result["net_applied_usd"], result["pending_usd"], result["unlinked_usd"]), (210, 50, 20))
        self.assertEqual(result["overdue_usd"], 50)

    def test_partial_cash_and_rounding_do_not_cancel_other_clients(self):
        result = self.figures([
            self.source(amount=46.53, historical_remitted_usd=46.52,
                        historical_detail=json.dumps([{"diferencia_usd": -0.01}])),
            self.source(name="EXCESS", amount=100, historical_remitted_usd=150),
            self.source(name="UNPAID", amount=25),
        ])
        self.assertEqual(result["pending_usd"], 25)
        self.assertEqual(result["net_applied_usd"], 171.53)

    def test_operational_group_counts_cash_once_and_total_keeps_paid_applications(self):
        collections = {"C": {"name": "C", "parent": "P", "remittance_detail": json.dumps([
            {"destino": "Cobranza", "importe_usd": 40},
            {"destino": "Partida complementaria", "importe_usd": 15}])}}
        sources = [self.source(name=str(i), historical_period="", collection_row_id="C", amount=amount)
                   for i, amount in enumerate([60, 40])]
        result = self.figures(sources, collections)
        self.assertEqual((result["net_applied_usd"], result["pending_usd"]), (100, 60))
        collections["C"]["remittance_detail"] = '[{"importe_usd":100}]'
        result = self.figures(sources, collections)
        self.assertEqual((result["net_applied_usd"], result["pending_usd"]), (100, 0))

    def test_scope_uses_payroll_month_or_unlinked_application_and_employer(self):
        result = self.figures([self.source(event_date="2026-01-01"),
                               self.source(historical_period="", event_date="2026-01-01")])
        self.assertEqual(result["net_applied_usd"], 100)
        self.assertEqual(self.figures([self.source()], employer="OTHER")["net_applied_usd"], 0)
        self.assertEqual(self.figures([self.source()], today="2025-05-10")["overdue_usd"], 0)

    def test_missing_conversion_is_flagged_not_reported_as_a_complete_zero(self):
        result = self.figures([self.source(currency="NIO", manual_fx_rate=0)])
        self.assertEqual(result["missing_fx_count"], 1)

    def test_future_months_are_excluded_like_the_aging_report(self):
        rows = [{"application_date": "2027-01-01", "applied_usd": 100, "amount_usd": 100}]
        result = kpis.summarize_applications(rows, as_of="2026-10-02")
        self.assertEqual(result["net_applied_usd"], 0)
        self.assertEqual(result["pending_usd"], 0)

    def test_deposits_full_amounts_unique_and_no_offset_between_deposits(self):
        deposit = {"name": "D", "amount_usd": 1000, "allocated_usd": 900, "justified_surplus_usd": 100}
        result = kpis.summarize_deposits([deposit, deposit,
            {"name": "D2", "amount_usd": 20, "allocated_usd": 0},
            {"name": "D3", "amount_usd": 10, "allocated_usd": 30}])
        self.assertEqual(result, {"received_usd": 1030, "deposit_count": 3,
            "unassigned_usd": 20, "unassigned_count": 1, "overallocated_count": 1})

    def test_credits_exclude_resolved_client_amount_and_retain_company_limitation(self):
        client = {"name": "C", "category": "Saldo a favor del cliente", "amount_usd": 100,
                  "credit_pending_usd": 40, "credit_management_status": "Parcial"}
        result = kpis.summarize_credits([client, client,
            dict(client, name="DONE", credit_pending_usd=0, credit_management_status="Resuelto"),
            dict(client, name="OLD", amount_usd=5, credit_management_status="", credit_pending_usd=0),
            {"name": "COMPANY", "category": "Saldo a favor de la empresa", "amount_usd": 10}])
        self.assertEqual(result, {"client_pending_usd": 45, "company_documented_usd": 10, "credit_pending_usd": 55})

    def test_unavailable_permissions_are_not_zero_and_never_query_children(self):
        with patch.object(kpis.frappe, "has_permission", return_value=False), \
             patch.object(kpis.frappe, "get_list") as get_list, patch.object(kpis.frappe, "get_all") as get_all:
            result = kpis.get_figures(2025, None, date(2026, 10, 2))
        self.assertTrue(all(result[key] is None for key in ("applications", "deposits", "credits", "exceptions")))
        get_list.assert_not_called()
        get_all.assert_not_called()

    def test_loader_permission_scopes_children_and_does_not_filter_before_grouping(self):
        def get_list(doctype, **kwargs):
            self.assertEqual(kwargs["limit_page_length"], 0)
            if doctype == "CN Accounting Import":
                return [frappe._dict(name="I", employer="E")]
            if doctype == "CN Reconciliation Period":
                return [frappe._dict(name="H", employer="E", payroll_month="2025-04-01")]
            return []

        def get_all(doctype, **kwargs):
            if doctype == "CN Source Row":
                self.assertEqual(kwargs["filters"]["parent"], ["in", ["I"]])
                self.assertNotIn("event_date", kwargs["filters"])
                return [frappe._dict(self.source())]
            if doctype == "CN Collection Row":
                self.assertEqual(kwargs["filters"]["parent"], ["in", ["H"]])
            if doctype == "CN Employer":
                return [frappe._dict(name="E", grace_days=10)]
            return []

        with patch.object(kpis.frappe, "has_permission", return_value=True), \
             patch.object(kpis.frappe, "get_list", side_effect=get_list), \
             patch.object(kpis.frappe, "get_all", side_effect=get_all):
            self.assertEqual(kpis.load_application_figures(2025, "E", date(2025, 5, 11))["pending_usd"], 100)

    def test_exception_filters_include_unlinked_exclude_resolved_and_match_navigation(self):
        filters = kpis.overdue_filters(2025, "E", "2025-05-10")
        self.assertEqual(filters["commitment_date"], ["between", ["2025-01-01", "2025-05-09"]])
        self.assertNotIn("period", filters)
        self.assertEqual(filters["status"], ["in", ["Abierta", "En revision"]])
        self.assertIsNone(kpis.overdue_filters(2027, None, "2026-10-02"))
        self.assertEqual(kpis.overdue_filters(None, None, "2026-10-02")["commitment_date"], ["<=", "2026-10-01"])

    def test_endpoint_validates_scope_and_does_not_reconcile(self):
        with patch.object(page.frappe, "has_permission", return_value=True), \
             patch.object(page, "now_datetime", return_value=datetime(2026, 10, 2)), \
             patch.object(kpis, "get_figures", return_value={}) as figures:
            page.get_control_kpis("Todos", "E")
        self.assertEqual(figures.call_args.args[:2], (None, "E"))
        with patch.object(page.frappe, "has_permission", return_value=False), \
             patch.object(page.frappe, "throw", side_effect=PermissionError), \
             patch.object(kpis, "get_figures") as figures:
            with self.assertRaises(PermissionError):
                page.get_control_kpis("2025", "E")
        figures.assert_not_called()


if __name__ == "__main__":
    unittest.main()
