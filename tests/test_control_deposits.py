import json
import unittest
from unittest.mock import patch

from credinomina_reconciliation import control_deposits as cash


def deposit(**values):
    return dict(name="D1", employer="E", deposit_date="2025-05-20", bank_account="BANPRO 3268 C$",
                deposit_currency="NIO", deposit_amount=36624.30, amount_usd=1000,
                allocated_usd=900, justified_surplus_usd=100,
                result="Parcial con saldo a favor", allocation_detail=json.dumps([
                    {"tipo": "Aplicacion historica", "periodo": "P1", "importe_usd": 800},
                    {"tipo": "Partida complementaria", "partida": "X1", "importe_usd": 100},
                ])) | values


class ControlDepositsTests(unittest.TestCase):
    def test_whole_deposit_not_only_credits_and_receipt_month_not_payroll(self):
        row, = cash.build_cash_deposits([deposit()],
            {"X1": dict(name="X1", category="Cobranza administrativa", period="P1")},
            {"P1": dict(name="P1", payroll_month="2025-04-01")})
        self.assertEqual(row["month"], "2025-05")
        self.assertEqual(row["bank_account"], "BANPRO 3268 C$")
        self.assertEqual(row["payroll_months"], ["2025-04"])
        self.assertEqual([row[k] for k in ("total_usd", "credits_usd", "other_usd", "credit_balance_usd")], [1000, 800, 100, 100])
        self.assertFalse(row["settled"])
        self.assertFalse(row["needs_review"])
        self.assertIn("Cobranza administrativa", row["destinations"][1]["label"])

    def test_two_periods_and_duplicate_deposit_count_once(self):
        d = deposit(allocated_usd=1000, justified_surplus_usd=0, result="Conciliado", allocation_detail=[
            {"tipo": "Cobranza", "periodo": "P1", "importe_usd": 400},
            {"tipo": "Aplicacion historica", "periodo": "P2", "importe_usd": 600}])
        rows = cash.build_cash_deposits([d, d], periods={
            "P1": dict(name="P1", payroll_month="2025-03-01"),
            "P2": dict(name="P2", payroll_month="2025-04-01")})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["shared"])
        self.assertTrue(rows[0]["settled"])
        self.assertEqual(len(rows[0]["destinations"]), 2)
        self.assertEqual(rows[0]["payroll_months"], ["2025-03", "2025-04"])

    def test_no_period_unassigned_and_unknown_differences_stay_visible(self):
        for allocation in ("broken json", "{}", "[null, 1]", "[]"):
            row, = cash.build_cash_deposits([deposit(allocation_detail=allocation)])
            self.assertEqual(row["review_usd"], 900)
            self.assertTrue(row["needs_review"])
            self.assertFalse(row["settled"])
        row, = cash.build_cash_deposits([deposit(allocated_usd=0, justified_surplus_usd=0,
                                               allocation_detail="[]", result="Sin aplicación")])
        self.assertEqual(row["unclassified_usd"], 1000)
        self.assertEqual(row["month"], "2025-05")

    def test_full_assignment_does_not_override_pending_detail_status(self):
        row, = cash.build_cash_deposits([deposit(amount_usd=900, justified_surplus_usd=0, result="Revisar detalle")])
        self.assertTrue(row["needs_review"])
        self.assertFalse(row["settled"])

    def test_signed_items_and_rounding_balance_cash_exactly(self):
        d = deposit(amount_usd=90.01, allocated_usd=90.01, justified_surplus_usd=0,
                    result="Conciliado", allocation_detail=[
                        {"tipo": "Cobranza", "periodo": "P1", "importe_usd": 100},
                        {"tipo": "Partida complementaria", "partida": "X1", "importe_usd": -10},
                        {"tipo": "Movimiento de conciliación", "periodo": "P1", "importe_usd": 0.01},
                    ])
        row, = cash.build_cash_deposits([d])
        self.assertEqual(row["other_usd"], -10)
        self.assertEqual(row["adjustments_usd"], 0.01)
        self.assertEqual(row["review_usd"], 0)
        self.assertEqual(row["unclassified_usd"], 0)
        self.assertTrue(row["settled"])

    def test_unavailable_related_documents_are_not_leaked(self):
        row, = cash.build_cash_deposits([deposit()])
        encoded = json.dumps(row)
        self.assertNotIn("P1", encoded)
        self.assertNotIn("X1", encoded)
        self.assertEqual(row["payroll_months"], [])
        self.assertEqual(row["credits_usd"], 800)

    def test_loading_uses_receipt_year_all_companies_and_no_period_requirement(self):
        calls = []
        def get_list(dt, **kw):
            calls.append((dt, kw))
            return [deposit()] if dt == "CN Remittance Allocation" else []
        with patch.object(cash.frappe, "has_permission", return_value=True), \
                patch.object(cash.frappe, "get_list", side_effect=get_list):
            rows = cash.get_cash_deposits(2025)
        self.assertEqual(len(rows), 1)
        filters = calls[0][1]["filters"]
        self.assertEqual(filters, {"docstatus": 1, "deposit_date": ["between", ["2025-01-01", "2025-12-31"]]})
        self.assertEqual(calls[0][1]["limit_page_length"], 0)
        self.assertIn("bank_account", calls[0][1]["fields"])
        # Related periods are queried by identity, without restricting their year.
        period_query = next(q for dt, q in calls if dt == "CN Reconciliation Period")
        self.assertEqual(period_query["filters"], {"name": ["in", ["P1"]]})

    def test_all_years_and_selected_company(self):
        with patch.object(cash.frappe, "has_permission", return_value=True), \
                patch.object(cash.frappe, "get_list", return_value=[]) as query:
            self.assertEqual(cash.get_cash_deposits(None, "E"), [])
        self.assertEqual(query.call_args.kwargs["filters"], {"docstatus": 1, "employer": "E"})

    def test_missing_read_permission_is_not_zero_cash(self):
        with patch.object(cash.frappe, "has_permission", return_value=False), \
                patch.object(cash.frappe, "get_list") as query:
            self.assertIsNone(cash.get_cash_deposits(2025))
        query.assert_not_called()
