import json
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation import company_conversion as company
from credinomina_reconciliation.accounting_client_summary import build_summary, balance_totals
from credinomina_reconciliation.deposit_distribution import build_distribution
from credinomina_reconciliation.deposit_reconciliation import frozen_cash, assert_preserved_allocations
from credinomina_reconciliation.period_cash_breakdown import calculate
from credinomina_reconciliation.reconciliation_report import build_views
from credinomina_reconciliation.remittance_row_reconciliation import split_row_allocations


class CompanyDifferenceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.proof = dict(deposit="DEP", employer="EMP", period="P", difference_row="DIFF",
                          amount_usd=-.01, application_ids=["A", "B"], difference_reason="Manual")
        self.journal = [dict(tipo="Aplicacion historica", periodo="P", aplicacion_id=name, importe_usd=amount)
                        for name, amount in (("A", 60), ("B", 40))]
        self.journal.append(company.journal_entry(self.proof))
        self.deposit = frappe._dict(name="DEP", docstatus=1, amount_usd=99.99, allocated_usd=99.99,
            allocation_detail=json.dumps(self.journal), result="Conciliado con diferencia documentada")

    def sources(self):
        return [dict(name=name, parent="I", event_type="Aplicacion", effective=1,
            historical_period="P", match_status="Conciliado", client_number=name, client_name=name,
            loan_number="LOAN-" + name, currency="USD", amount=amount,
            historical_remitted_usd=amount,
            historical_detail=[dict(importe_usd=amount, conversion_empresa=self.proof)])
            for name, amount in (("A", 60), ("B", 40))]

    def test_frozen_cash_preserves_every_loan_and_keeps_company_entry_separate(self):
        ledger, coverage = frozen_cash([self.deposit], [], [])
        self.assertEqual(dict(coverage), {"H:A": 60, "H:B": 40})
        self.assertEqual(sum(row["amount_usd"] for row in ledger), 99.99)
        self.assertEqual(ledger[-1]["company_conversion"], self.proof)
        # A later actual core counterpart may replace the documentary company
        # entry, but neither client's existing link may be reduced or swapped.
        replacement = [dict(row) for row in ledger[:2]] + [dict(deposit_id="DEP", claim_id="X:CORE", amount_usd=-.01)]
        assert_preserved_allocations(self.deposit, {"allocations": replacement}, [])
        replacement[0]["amount_usd"] = 59.99
        with patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
            assert_preserved_allocations(self.deposit, {"allocations": replacement}, [])

    def test_distribution_has_no_customer_on_company_difference_and_cash_reconciles(self):
        view = build_distribution(self.deposit)
        self.assertTrue(view["consistent"])
        self.assertEqual(view["distributed_usd"], 99.99)
        row = next(row for row in view["rows"] if row["category"] == "Diferencia documentada de la empresa")
        self.assertEqual(row["amount_usd"], -.01)
        self.assertFalse(row["client_name"] or row["client_number"] or row["loan_number"])
        with patch.object(frappe, "throw", side_effect=ValueError) as reject, self.assertRaises(ValueError):
            split_row_allocations(self.deposit, "CLIENT-ROW")
        self.assertIn("nivel de empresa", reject.call_args.args[0])

    def test_period_distinguishes_company_documentation_from_real_complementary_items(self):
        ledger, _coverage = frozen_cash([self.deposit], [], [])
        result = calculate("P", ledger, {"H:A": "P", "H:B": "P"})
        self.assertTrue(result["known"])
        self.assertEqual(result["cash_adjustment_usd"], -.01)
        self.assertEqual(result["negative_complementary_usd"], 0)
        self.assertEqual(result["conversion_coverage_usd"], .01)

    def test_client_balances_stay_whole_and_import_cash_is_not_overstated(self):
        sources = self.sources()
        customers = build_summary({"rows": sources}, {})
        self.assertEqual([row["assigned_usd"] for row in customers], [60, 40])
        self.assertTrue(all(row["balance_usd"] == 0 for row in customers))
        totals = balance_totals({"rows": sources}, customers)
        self.assertEqual((totals["deposits_usd"], totals["conversion_usd"], totals["balance_usd"]), (99.99, .01, 0))
        shared = balance_totals({"rows": sources[:1]}, customers[:1])
        self.assertIsNone(shared["deposits_usd"])
        self.assertIsNone(shared["conversion_usd"])

    def test_report_adds_one_company_row_instead_of_discounting_a_customer(self):
        period = dict(name="P", employer="EMP", reconciliation_mode="Historica", payroll_month="2026-03-01", applied_usd=100)
        summary, details = build_views([period], [], self.sources())
        self.assertEqual(summary[0]["credit_count"], 2)
        self.assertEqual((summary[0]["deposit_usd"], summary[0]["conversion_usd"], summary[0]["open_usd"]), (99.99, .01, 0))
        employer = next(row for row in details if row.get("_company_conversion"))
        self.assertEqual((employer["deposit_usd"], employer["difference_reason"]), (-.01, "Manual"))
        self.assertFalse(employer["client_number"] or employer["loan_number"])
        self.assertEqual(sum(row["open_usd"] for row in details), 0)

    def test_corrupt_company_documentation_cannot_fabricate_cash_in_reports(self):
        sources = self.sources()
        sources[0]['historical_detail'][0]['conversion_empresa'] = dict(self.proof, amount_usd=-1)
        with self.assertRaises(ValueError):
            company.from_rows(sources)
        period = dict(name="P", employer="EMP", reconciliation_mode="Historica", payroll_month="2026-03-01", applied_usd=100)
        summary, _details = build_views([period], [], sources)
        self.assertIsNone(summary[0]['deposit_usd'])
        self.assertIsNone(summary[0]['open_usd'])
