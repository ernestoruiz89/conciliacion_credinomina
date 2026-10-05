import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation import complementary_transactions as transactions
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.conciliacion_credinomina.report.transacciones_por_empresa import transacciones_por_empresa as report


class ComplementaryTransactionsTests(unittest.TestCase):
    def item(self, **values):
        return frappe._dict(dict(name="C1", employer="A", category="Otros ingresos", amount_usd=100, docstatus=1,
            source_date="2025-04-15", posting_date="2025-05-01", client_number="1", source_client_name="Ana") | values)

    def test_progress_uses_confirmed_distributions_and_correct_signed_direction(self):
        for amount, allocated, expected in ((100, 0, "Pendiente"), (100, 25, "Parcial"), (100, 100, "Conciliado"),
                                            (-100, -50, "Parcial"), (-100, -100, "Conciliado"), (100, 110, "Parcial")):
            with self.subTest(amount=amount, allocated=allocated):
                item = self.item(amount_usd=amount)
                balance = financial_balance(item, [{"amount_usd": allocated}])
                result = transactions.progress(item, balance)
                self.assertEqual(result["state"], expected)
                self.assertEqual(result["original_usd"], 100)
                self.assertLessEqual(result["resolved_usd"], 100)

    def test_compensation_application_adjustment_credits_and_internal_tolerance(self):
        cases = [
            (dict(category="Compensación entre partidas", docstatus=0, compensated_usd=60), "Parcial", 60),
            (dict(category="Compensación entre partidas", docstatus=0, compensated_usd=100), "Conciliado", 100),
            (dict(category="Ajuste de aplicación", related_application="R", application_adjustment_usd=40), "Parcial", 40),
            (dict(category="Ajuste de aplicación", related_application="R", application_adjustment_usd=100), "Conciliado", 100),
            (dict(category="Saldo a favor del cliente", result="Saldo a favor documentado", credit_pending_usd=100), "Conciliado", 100),
            (dict(category="Saldo a favor de la empresa", result="Saldo a favor documentado", credit_pending_usd=100), "Conciliado", 100),
            (dict(category="Diferencia por tolerancia", status="Vigente"), "Conciliado", 100),
            (dict(docstatus=0, registration_exception="EXC"), "Conciliado", 100),
            (dict(docstatus=0, review_action="No conciliatoria", review_status="No conciliatoria"), "Conciliado", 100),
            (dict(docstatus=0, review_action="Reversión identificada", related_application="R"), "Pendiente", 0),
        ]
        for values, state, resolved in cases:
            with self.subTest(values=values):
                item = self.item(**values)
                progress = transactions.progress(item, financial_balance(item))
                self.assertEqual((progress["state"], progress["resolved_usd"]), (state, resolved))

    def test_two_origins_are_mutually_exclusive_and_use_their_own_date(self):
        for kind, accounting in transactions.KINDS.items():
            with self.subTest(kind=kind):
                item = self.item(category="Compensación entre partidas", docstatus=0, compensated_usd=60)
                with patch.object(frappe, "get_list", return_value=[item]) as get_list:
                    records, rows, _ = transactions.load_transactions(kind, False, {"employer": "A"}, ["2025-01-01", "2025-12-31"])
                filters = get_list.call_args.kwargs["filters"]
                self.assertEqual(get_list.call_args.args[0], "CN Complementary Item")
                self.assertEqual(filters["accounting_source_key"], ["is", "set" if accounting else "not set"])
                self.assertEqual(filters["docstatus"], ["!=", 2])
                self.assertEqual(filters["employer"], "A")
                self.assertIn("source_date" if accounting else "posting_date", filters)
                self.assertEqual(records[0]["date"], "2025-04-15" if accounting else "2025-05-01")
                self.assertEqual(rows[0]["client_name"], "Ana")
                self.assertEqual(records[0]["state"], "Parcial")

    def test_draft_filter_does_not_hide_confirmed_offsets_and_reverted_tolerances_are_excluded(self):
        items = [self.item(name="draft", docstatus=0),
                 self.item(name="offset", docstatus=0, category="Compensación entre partidas", compensated_usd=50),
                 self.item(name="reverted", category="Diferencia por tolerancia", status="Revertido")]
        for drafts, expected in ((False, ["offset"]), (True, ["draft", "offset"])):
            with patch.object(frappe, "get_list", return_value=items), patch.object(transactions, "load_balances") as cash:
                records, _, _ = transactions.load_transactions("Partidas complementarias contables", drafts, {}, [])
            self.assertEqual([row["name"] for row in records], expected)
            cash.assert_not_called()

    def test_cash_uses_actual_distribution_in_any_month_not_selected_targets(self):
        item = self.item()
        deposit = frappe._dict(name="D", deposit_date="2026-01-01", allocation_detail='[{"tipo":"Partida complementaria","partida":"C1","importe_usd":60}]')
        def get_list(doctype, **kwargs):
            if doctype == "CN Complementary Item":
                return [item]
            self.assertEqual(doctype, "CN Remittance Allocation")
            self.assertEqual(kwargs["filters"], {"docstatus": 1,
                "allocation_detail": ["like", '%"Partida complementaria"%']})
            self.assertNotIn("deposit_date", kwargs["filters"])
            return [deposit]
        with patch.object(frappe, "get_list", side_effect=get_list), patch.object(frappe, "has_permission", return_value=True):
            records, items, _ = transactions.load_transactions("Partidas complementarias contables", False, {}, [])
        self.assertEqual(records[0]["state"], "Parcial")
        self.assertEqual(items[0]["_progress"]["pending_usd"], 40)
        self.assertEqual(transactions.month_amounts(items)[("A", "2025-04")], {"total_usd": 100, "covered_usd": 60})

    def test_cash_permission_does_not_leak_or_invent_zero_balance(self):
        with patch.object(frappe, "get_list", return_value=[self.item()]), \
                patch.object(frappe, "has_permission", return_value=False), patch.object(transactions, "load_balances") as cash:
            _, items, _ = transactions.load_transactions("Partidas complementarias contables", False, {}, [])
        cash.assert_not_called()
        self.assertIsNone(items[0]["_progress"]["pending_usd"])
        self.assertIsNone(transactions.month_amounts(items)[("A", "2025-04")]["covered_usd"])

    def test_absolute_weighting_does_not_cancel_opposite_signs(self):
        items = [self.item(amount_usd=value, category="Compensación entre partidas", compensated_usd=50, docstatus=0) for value in (100, -100)]
        with patch.object(frappe, "get_list", return_value=items):
            _, rows, _ = transactions.load_transactions("Partidas complementarias contables", False, {}, [])
        self.assertEqual(transactions.month_amounts(rows)[("A", "2025-04")], {"total_usd": 200, "covered_usd": 100})

    def test_report_detail_totals_counts_and_permission_check_for_both_kinds(self):
        for kind in transactions.KINDS:
            item = self.item(docstatus=0, category="Compensación entre partidas", compensated_usd=50)
            with patch.object(frappe, "has_permission", return_value=True), patch.object(frappe, "get_list", return_value=[item]):
                _, data, _, _, summary = report.execute(dict(year=2025, transaction_type=kind))
                result = report.get_month_detail(2025, 4 if transactions.KINDS[kind] else 5, "A", transaction_type=kind, search="Ana")
            self.assertEqual(data[0]["total"], 1)
            self.assertEqual(data[-1]["total"], 1)
            self.assertEqual(summary[1]["value"], 1)
            self.assertEqual(result["totals"], dict(original_usd=100, resolved_usd=50, pending_usd=50))
            self.assertEqual(result["rows"][0]["doctype"], "CN Complementary Item")
            with patch.object(frappe, "has_permission", return_value=False), \
                    patch.object(frappe, "throw", side_effect=ValueError), patch.object(frappe, "get_list") as query:
                with self.assertRaises(ValueError):
                    report.execute(dict(year=2025, transaction_type=kind))
                with self.assertRaises(ValueError):
                    report.get_month_detail(2025, 4, "A", transaction_type=kind)
            query.assert_not_called()
