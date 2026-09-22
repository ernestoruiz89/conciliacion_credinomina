import unittest

from credinomina_reconciliation.allocation import allocate_cash
from credinomina_reconciliation.historical import (
    blocked_historical_deposits,
    historical_balance,
    historical_status,
    is_historical_date,
)
from credinomina_reconciliation.reconciliation import (
    duplicate_business_key,
    narrow_deposit_candidates_by_date,
)


class HistoricalReconciliationTest(unittest.TestCase):
    def test_historical_window_and_unmatched_application_balance(self):
        self.assertFalse(is_historical_date("2025-03-31"))
        self.assertTrue(is_historical_date("2025-04-01"))
        self.assertTrue(is_historical_date("2026-08-31"))
        self.assertFalse(is_historical_date("2026-09-01"))
        self.assertEqual(10, historical_balance(100, 90))
        self.assertEqual("Historico parcial", historical_status(100, 90))
        self.assertEqual("Historico conciliado", historical_status(100, 100))
        self.assertEqual("Historico pendiente", historical_status(100, 0))

    def test_one_deposit_can_cover_several_historical_applications(self):
        deposits = [{"id": "D1", "reference": "REF-A", "amount_usd": 100, "group": "EMP"}]
        claims = [
            {"id": "H:A1", "references": ["REF-A"], "amount_usd": 40, "group": "EMP", "period": "APR"},
            {"id": "H:A2", "references": ["REF-A"], "amount_usd": 60, "group": "EMP", "period": "APR"},
        ]
        self.assertEqual(set(), blocked_historical_deposits(claims, deposits))
        result = allocate_cash(deposits, claims)
        self.assertEqual(0, result["deposit_remaining"]["D1"])
        self.assertEqual(2, len(result["allocations"]))

    def test_several_deposits_can_cover_one_historical_application(self):
        deposits = [
            {"id": "D1", "reference": "R1", "amount_usd": 40, "group": "EMP"},
            {"id": "D2", "reference": "R1", "amount_usd": 60, "group": "EMP"},
        ]
        claims = [{"id": "H:A1", "references": ["R1"], "amount_usd": 100, "group": "EMP", "period": "APR"}]
        result = allocate_cash(deposits, claims)
        self.assertEqual(0, result["claim_remaining"]["H:A1"])
        self.assertEqual(2, len(result["allocations"]))

    def test_reused_reference_requires_manual_allocation(self):
        deposits = [{"id": "D1", "reference": "REUSED", "amount_usd": 50, "group": "EMP"}]
        claims = [
            {"id": "H:A1", "references": ["REUSED"], "amount_usd": 50, "group": "EMP", "period": "APR"},
            {"id": "H:A2", "references": ["REUSED"], "amount_usd": 50, "group": "EMP", "period": "MAY"},
        ]
        blocked = blocked_historical_deposits(claims, deposits)
        self.assertEqual({"D1"}, blocked)
        self.assertEqual([], allocate_cash(deposits, claims, blocked_deposit_ids=blocked)["allocations"])
        result = allocate_cash(
            deposits, claims,
            [{"id": "M1", "deposit_id": "D1", "claim_id": "H:A2", "amount_usd": 50}],
            blocked_deposit_ids=blocked,
        )
        self.assertEqual("Aplicada", result["instruction_results"]["M1"])
        self.assertEqual("H:A2", result["allocations"][0]["claim_id"])

    def test_historical_and_operative_claim_cannot_compete_automatically(self):
        deposits = [{"id": "D1", "reference": "SHARED", "amount_usd": 90}]
        claims = [
            {"id": "H:A1", "references": ["SHARED"], "amount_usd": 90, "period": "AUG"},
            {"id": "C:R1", "references": ["SHARED"], "amount_usd": 90},
        ]
        self.assertEqual({"D1"}, blocked_historical_deposits(claims, deposits))

    def test_two_deposits_and_two_applications_with_one_reference_need_review(self):
        deposits = [
            {"id": "D1", "reference": "REUSED", "amount_usd": 40},
            {"id": "D2", "reference": "REUSED", "amount_usd": 60},
        ]
        claims = [
            {"id": "H:A1", "references": ["REUSED"], "amount_usd": 40, "period": "APR"},
            {"id": "H:A2", "references": ["REUSED"], "amount_usd": 60, "period": "APR"},
        ]
        self.assertEqual({"D1", "D2"}, blocked_historical_deposits(claims, deposits))

    def test_repeated_bank_reference_can_be_distinguished_by_exact_date_only(self):
        candidates = [
            {"id": "B1", "event_date": "2025-05-10"},
            {"id": "B2", "event_date": "2026-05-10"},
        ]
        self.assertEqual(
            [candidates[0]],
            narrow_deposit_candidates_by_date({"event_date": "2025-05-10"}, candidates),
        )
        self.assertEqual(
            candidates,
            narrow_deposit_candidates_by_date({"event_date": "2025-05-11"}, candidates),
        )

    def test_repeated_application_reference_in_another_month_is_not_a_duplicate(self):
        first = {
            "event_type": "Aplicacion", "loan_number": "100",
            "reference": "R-1", "currency": "USD", "amount": 25,
            "event_date": "2025-05-10", "voucher": "V-1",
        }
        second = {**first, "event_date": "2025-06-10"}
        third = {**first, "voucher": "V-2"}
        self.assertNotEqual(duplicate_business_key(first), duplicate_business_key(second))
        self.assertNotEqual(duplicate_business_key(first), duplicate_business_key(third))


if __name__ == "__main__":
    unittest.main()
