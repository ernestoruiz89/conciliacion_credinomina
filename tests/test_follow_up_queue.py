import unittest
from credinomina_reconciliation.follow_up_queue import build_follow_up, merge_follow_up, filter_work
from credinomina_reconciliation.complementary_balances import financial_balance


class FollowUpQueueTests(unittest.TestCase):
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
