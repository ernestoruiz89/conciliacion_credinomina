import unittest

from credinomina_reconciliation.allocation import allocate_cash
from credinomina_reconciliation.remittance_detail import (
    detail_amount_usd,
    suggest_detail_targets,
)


def claim(index, amount=1, *, kind="H", installment="1", row_key=""):
    return {
        "id": f"{kind}:{index}", "kind": kind, "amount_usd": amount,
        "group": "Empresa A", "period": "Abril 2025",
        "client_number": str(index), "national_id": f"CED-{index}",
        "loan_number": f"CRE-{index}", "installment_number": installment,
        "row_key": row_key, "references": ["AP-1"], "hints": {},
    }


def detail(index, **extra):
    return {
        "client_number": str(index), "national_id": f"CED-{index}",
        "loan_number": f"CRE-{index}", "installment_number": "1",
        **extra,
    }


class RemittanceDetailTests(unittest.TestCase):
    def test_usd_is_authoritative_and_nio_is_not_added(self):
        self.assertEqual(
            detail_amount_usd({"deducted_usd": 46.52, "deducted_nio": 1700}, 36.55)[0],
            46.52,
        )

    def test_nio_only_needs_documented_rate(self):
        row = {"deducted_nio": 3600}
        self.assertEqual(detail_amount_usd(row, 0)[0], 0)
        self.assertIn("Falta tasa", detail_amount_usd(row, 0)[1])
        self.assertEqual(detail_amount_usd(row, 36)[0], 100)

    def test_two_hundred_applications_one_deposit(self):
        claims = [claim(index) for index in range(1, 201)]
        instructions = []
        for index in range(1, 201):
            targets, reason = suggest_detail_targets(
                detail(index), claims, 1, "Empresa A"
            )
            self.assertEqual(reason, "Coincidencia única")
            self.assertEqual(len(targets), 1)
            instructions.append({
                "id": f"D:{index}", "deposit_id": "deposito",
                **targets[0],
            })
        result = allocate_cash(
            [{"id": "deposito", "reference": "AP-1", "amount_usd": 200,
              "group": "Empresa A"}],
            claims, instructions, blocked_deposit_ids={"deposito"},
        )
        self.assertEqual(len(result["allocations"]), 200)
        self.assertEqual(result["deposit_remaining"]["deposito"], 0)

    def test_wrong_client_does_not_match_same_loan(self):
        row = detail(1, client_number="999", national_id="CED-1")
        targets, _ = suggest_detail_targets(row, [claim(1)], 1, "Empresa A")
        self.assertEqual(targets, [])

    def test_primary_accounting_without_client_uses_unique_loan_with_warning(self):
        destination = claim(1)
        destination["client_number"] = ""
        destination["national_id"] = ""
        targets, reason = suggest_detail_targets(
            detail(1), [destination], 1, "Empresa A"
        )
        self.assertEqual(len(targets), 1)
        self.assertIn("core sin identidad", reason)

    def test_two_installments_only_when_exact_total(self):
        first = claim(1, 4, installment="1")
        second = claim(1, 6, installment="2")
        row = detail(1, installment_number="")
        targets, _ = suggest_detail_targets(row, [first, second], 10, "Empresa A")
        self.assertEqual(len(targets), 2)
        targets, _ = suggest_detail_targets(row, [first, second], 4, "Empresa A")
        self.assertEqual(targets, [])

    def test_operational_and_historical_overlap_needs_period(self):
        operational = claim(1, kind="C", row_key="F1")
        historical = claim(1, kind="H")
        targets, reason = suggest_detail_targets(
            detail(1), [operational, historical], 2, "Empresa A"
        )
        self.assertEqual(targets, [])
        self.assertIn("histórico", reason)

    def test_company_row_can_include_administrative_fee(self):
        loan = claim(1, 90, kind="C", row_key="F1")
        fee = claim(1, 10, kind="X")
        targets, _ = suggest_detail_targets(
            detail(1, row_key="F1"), [loan, fee], 100, "Empresa A"
        )
        self.assertEqual({target["claim_id"] for target in targets}, {"C:1", "X:1"})

    def test_partial_single_application_and_overpayment(self):
        targets, _ = suggest_detail_targets(detail(1), [claim(1, 100)], 40, "Empresa A")
        self.assertEqual(targets[0]["amount_usd"], 40)
        targets, _ = suggest_detail_targets(detail(1), [claim(1, 100)], 101, "Empresa A")
        self.assertEqual(targets, [])

    def test_single_application_can_reserve_cent_difference_for_rounding(self):
        destination = claim(1, 46.52)
        destination["application_ids"] = ["APP-1"]
        targets, reason = suggest_detail_targets(
            detail(1), [destination], 46.53, "Empresa A", tolerance_usd=0.01
        )
        self.assertEqual(targets, [{"claim_id": "H:1", "amount_usd": 46.52}])
        self.assertIn("tolerancia", reason)
        targets, _ = suggest_detail_targets(
            detail(1), [destination], 46.54, "Empresa A", tolerance_usd=0.01
        )
        self.assertEqual(targets, [])


if __name__ == "__main__":
    unittest.main()
