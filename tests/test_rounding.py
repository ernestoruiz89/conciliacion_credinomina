import unittest

from credinomina_reconciliation.rounding import rounding_movements


class RoundingMovementTest(unittest.TestCase):
    def setUp(self):
        self.deposits = [{
            "id": "DEP-1", "amount_usd": 46.53, "bank_amount_usd": 46.53,
            "currency": "USD", "bank_currency": "USD", "group": "EMP-1",
        }]
        self.claims = [{
            "id": "C:ROW-1", "amount_usd": 46.52, "core_applied_usd": 46.52,
            "application_ids": ["APP-1"], "group": "EMP-1", "period": "PER-1",
        }]
        self.allocations = [{
            "deposit_id": "DEP-1", "claim_id": "C:ROW-1", "amount_usd": 46.52,
        }]

    def find(self, tolerance=0.01, remaining=0.01, claim_remaining=0):
        return rounding_movements(
            self.deposits, self.claims, self.allocations,
            {"DEP-1": remaining}, {"C:ROW-1": claim_remaining},
            {"EMP-1": tolerance},
        )

    def test_extra_cent_is_recorded_and_consumes_only_the_residual(self):
        movement = self.find()[0]
        self.assertEqual(0.01, movement["signed_amount_usd"])
        self.assertEqual(0.01, movement["consumed_residual_usd"])
        self.assertEqual("APP-1", movement["application_id"])
        self.assertEqual(movement["name"], self.find()[0]["name"])

    def test_extra_cent_already_inside_collection_claim_is_not_double_allocated(self):
        self.claims[0]["amount_usd"] = 46.53
        self.allocations[0]["amount_usd"] = 46.53
        movement = self.find(remaining=0)[0]
        self.assertEqual(0.01, movement["signed_amount_usd"])
        self.assertEqual(0, movement["consumed_residual_usd"])

    def test_short_cent_is_a_signed_movement_not_fictitious_cash(self):
        self.deposits[0]["amount_usd"] = self.deposits[0]["bank_amount_usd"] = 46.52
        self.claims[0]["amount_usd"] = self.claims[0]["core_applied_usd"] = 46.53
        movement = self.find(remaining=0, claim_remaining=0.01)[0]
        self.assertEqual(-0.01, movement["signed_amount_usd"])
        self.assertEqual(0, movement["consumed_residual_usd"])

    def test_zero_tolerance_or_larger_difference_stays_open(self):
        self.assertEqual([], self.find(tolerance=0))
        self.deposits[0]["amount_usd"] = self.deposits[0]["bank_amount_usd"] = 46.54
        self.assertEqual([], self.find())

    def test_many_to_many_or_fx_or_nonidentical_bank_pair_requires_review(self):
        self.allocations.append({
            "deposit_id": "DEP-1", "claim_id": "C:ROW-2", "amount_usd": 0.01,
        })
        self.assertEqual([], self.find())
        self.allocations.pop()
        self.deposits[0]["bank_currency"] = "NIO"
        self.assertEqual([], self.find())
        self.deposits[0]["bank_currency"] = "USD"
        self.deposits[0]["bank_amount_usd"] = 46.52
        self.assertEqual([], self.find())

    def test_no_core_application_or_blocked_reference_cannot_adjust(self):
        self.claims[0]["application_ids"] = []
        self.assertEqual([], self.find())
        self.claims[0]["application_ids"] = ["APP-1"]
        self.assertEqual([], rounding_movements(
            self.deposits, self.claims, self.allocations,
            {"DEP-1": 0.01}, {"C:ROW-1": 0}, {"EMP-1": 0.01}, ["DEP-1"],
        ))


if __name__ == "__main__":
    unittest.main()
