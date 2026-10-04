import unittest
from unittest.mock import patch
from credinomina_reconciliation.follow_up_queue import build_follow_up, merge_follow_up, filter_work, load_follow_up, load_credit_periods
from credinomina_reconciliation.complementary_balances import financial_balance


class FollowUpQueueTests(unittest.TestCase):
    def test_credit_periods_use_real_customer_allocations_and_respect_permissions(self):
        item = {"name": "C", "category": "Saldo a favor del cliente", "docstatus": 1,
                "employer": "EMP", "client_number": "00123", "registered_deposit": "DEP",
                "amount_usd": 10, "credit_pending_usd": 4, "credit_management_status": "Parcial",
                "result": "Saldo a favor documentado", "accounting_exception": "EXC"}
        entries = [{"tipo": "Aplicacion historica", "periodo": period, "aplicacion_id": period, "importe_usd": 10}
                   for period in ("P1", "P2", "OTHER-CLIENT", "OTHER-EMP", "HIDDEN")]
        entries += [{"tipo": "Partida complementaria", "periodo": "COMP", "importe_usd": 5}]
        people = {("H", period, period): {"client_number": "00123", "loan_number": loan}
                  for period, loan in (("P1", "L1"), ("P2", "L2"), ("OTHER-EMP", "L1"), ("HIDDEN", "L1"))}
        people[("H", "OTHER-CLIENT", "OTHER-CLIENT")] = {"client_number": "999", "loan_number": "L1"}
        def query(doctype, **kwargs):
            if doctype == "CN Remittance Allocation":
                self.assertEqual(kwargs["filters"]["docstatus"], 1)
                return [{"name": "DEP", "allocation_detail": entries}]
            self.assertEqual(doctype, "CN Reconciliation Period")
            return [{"name": period, "employer": "OTHER" if period == "OTHER-EMP" else "EMP"}
                    for period in ("P1", "P2", "OTHER-CLIENT", "OTHER-EMP")]
        with patch("credinomina_reconciliation.follow_up_queue.frappe.has_permission", return_value=True), \
                patch("credinomina_reconciliation.follow_up_queue.frappe.get_list", side_effect=query), \
                patch("credinomina_reconciliation.control_deposits._load_credit_people", return_value=people):
            related = load_credit_periods([item])
            self.assertEqual(related, {"C": ["P1", "P2"]})
            self.assertEqual(load_credit_periods([item | {"loan_number": "L1"}]), {"C": ["P1"]})
            self.assertEqual(load_credit_periods([item | {"client_number": "UNKNOWN"}]), {})
            self.assertEqual(load_credit_periods([item | {"credit_management_status": "Resuelto", "credit_pending_usd": 0}]), {})
        balance = {"C": financial_balance(item)}
        task = build_follow_up([item], balance, [], credit_periods=related)[0]
        self.assertEqual(task["period_label"], "P1 · P2")
        self.assertIsNone(task["period"])  # Display-only context, not an invented assignment of the credit.
        self.assertIn("vinculados al cliente", task["period_context"])
        own = build_follow_up([item | {"period": "DIRECT"}], balance, [], credit_periods=related)[0]
        self.assertEqual((own["period_label"], own["period_context"]), ("DIRECT", ""))
        self.assertEqual(build_follow_up([item], balance, [])[0]["period_label"], "Sin período")
        with patch("credinomina_reconciliation.follow_up_queue.frappe.has_permission", return_value=False), \
                patch("credinomina_reconciliation.follow_up_queue.frappe.get_list") as query:
            self.assertEqual(load_credit_periods([item]), {})
            query.assert_not_called()

    def test_client_credit_identity_is_loaded_with_pending_work_without_extra_queries(self):
        item = {"name": "CLIENT-CREDIT", "category": "Saldo a favor del cliente", "docstatus": 1,
                "amount_usd": 10, "result": "Saldo a favor documentado", "credit_pending_usd": 4,
                "credit_management_status": "Parcialmente resuelto", "accounting_exception": "EXC",
                "employer": "EMP", "client_name": "ANA PEREZ", "client_number": "00123"}
        with patch("credinomina_reconciliation.follow_up_queue.frappe.has_permission", side_effect=lambda dt, *_: dt == "CN Complementary Item"), \
                patch("credinomina_reconciliation.follow_up_queue.frappe.get_list", return_value=[item]) as query, \
                patch("credinomina_reconciliation.follow_up_queue.load_balances", return_value={item["name"]: financial_balance(item)}):
            tasks = load_follow_up(2025, "EMP")
        query.assert_called_once()
        self.assertIn("client_name", query.call_args.kwargs["fields"])
        self.assertIn("client_number", query.call_args.kwargs["fields"])
        self.assertEqual(query.call_args.kwargs["filters"]["employer"], "EMP")
        task = tasks[0]
        self.assertEqual((task["client_name"], task["client_number"]), ("ANA PEREZ", "00123"))
        self.assertEqual(task["category"], "Saldo a favor del cliente")
        self.assertEqual(task["amount_usd"], 4)
        self.assertEqual(task["target_name"], item["name"])

    def test_filter_applies_before_pagination_and_dates_are_independent(self):
        items = [{"kind": "complementary_balance", "responsible": "Ana", "due_date": "2026-10-01"}] * 150
        items += [{"kind": "credit_management", "responsible": "Ernesto", "due_date": None}]
        self.assertEqual(len(filter_work(items, "credits", "ern", "undated", "2026-10-03")), 1)
        self.assertEqual(len(filter_work(items, due="overdue", as_of="2026-10-03")), 150)
        self.assertEqual(filter_work(items, due="upcoming", as_of="2026-10-03"), [])
    def test_unlinked_credit_financial_and_accounting_work_remain_separate(self):
        item = {"name": "C", "category": "Saldo a favor de la empresa", "docstatus": 1, "amount_usd": 100,
            "result": "Saldo a favor documentado", "credit_pending_usd": 40, "credit_management_status": "Parcialmente resuelto",
            "credit_assigned_to": "operator", "credit_commitment_date": "2026-10-01"}
        rows = build_follow_up([item], {"C": financial_balance(item)}, [], as_of="2026-10-03")
        self.assertEqual({r["kind"] for r in rows}, {"credit_management", "accounting_registration"})
        credit = next(r for r in rows if r["kind"] == "credit_management")
        self.assertEqual((credit["amount_usd"], credit["period_label"], credit["priority"], credit["responsible"]), (40, "Sin período", 0, "operator"))

    def test_open_exception_without_period_included_and_not_duplicated(self):
        case = {"name": "X", "status": "Abierta", "commitment_date": "2026-10-01", "amount_usd": 1}
        rows = build_follow_up([], {}, [case], as_of="2026-10-03")
        existing = [{"target_doctype": "CN Reconciliation Exception", "target_name": "X", "priority": 0}]
        merged = merge_follow_up(existing, rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0]["kind"], merged[0]["period_label"]), ("open_exception", "Sin período"))

    def test_resolved_credit_does_not_generate_management_and_no_accounting_duplicate(self):
        item = {"name": "C", "category": "Saldo a favor del cliente", "docstatus": 1, "amount_usd": 10,
            "result": "Saldo a favor documentado", "credit_pending_usd": 0, "credit_management_status": "Resuelto", "accounting_exception": "X"}
        self.assertEqual(build_follow_up([item], {"C": financial_balance(item)}, []), [])
