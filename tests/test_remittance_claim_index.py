"""Indexed planning must preserve the exact decisions of the full claim scan."""
import copy
import itertools
import unittest
from unittest.mock import patch

from credinomina_reconciliation import remittance_detail as detail


def claim(name, **changes):
    return dict(id=f"H:{name}", kind="H", group="A", period="P1", amount_usd=10,
        client="CLIENT", client_number="12", national_id="ID", employee_number="EMP",
        client_name="Ana Pérez", client_names=["Ana P."], loan_number="00123-1",
        references=["REF"], application_ids=[name],
        fifo_applications=[dict(id=name, event_date="2025-01-15", amount_usd=10)]) | changes


class DetailClaimIndexTests(unittest.TestCase):
    def test_index_matches_full_scan_for_scopes_identities_and_ambiguities(self):
        claims = [claim("first"), claim("second", period="P2"), claim("payer", group="B"),
            claim("closed", period_closed=True), claim("manual", manual_only=True),
            claim("zero", amount_usd=0), claim("negative", amount_usd=-10),
            claim("cycle", loan_number="123-2"), claim("no-loan", loan_number=""),
            claim("person", client="OTHER", client_number="13"),
            claim("collection", kind="C", row_key="KEY", core_applied_usd=10),
            claim("complement", kind="X", id="X:complement", period="")]
        original = copy.deepcopy(claims)
        index = detail.DetailClaimIndex(claims)
        rows = [dict(loan_number="123"), dict(loan_number="000123-1", employer="A"),
            dict(loan_number="123-2"), dict(client_name="Ana P."),
            dict(client_name="No match"), dict(client_number="0012"),
            dict(client="OTHER", client_number="12", loan_number="123-1"),
            dict(national_id="CONFLICT", loan_number="123-1"),
            dict(employee_number="CONFLICT", loan_number="123-1"),
            dict(application_reference="CONFLICT", loan_number="123-1"),
            dict(loan_number="123-1", row_key="KEY"), dict(loan_number="123-1", row_key="MISSING"), {}]
        for row, periods, allowed, fifo in itertools.product(rows, ["", "P1", ["P1", "P2"]],
                [None, ["A"], ["A", "B"]], [False, True]):
            options = dict(period=periods, allowed_groups=allowed, apply_fifo=fifo)
            with self.subTest(row=row, periods=periods, allowed=allowed, fifo=fifo):
                expected = detail.suggest_detail_targets(row, claims, 10, "A", **options)
                actual = detail.suggest_detail_targets(row, claims, 10, "A", claim_index=index, **options)
                self.assertEqual(actual, expected)
        self.assertEqual(claims, original)

    def test_duplicate_collection_key_is_not_hidden_by_credit_index(self):
        claims = [claim("one", kind="C", row_key="DUP"),
                  claim("two", kind="C", row_key="DUP", loan_number="999-1")]
        result = detail.suggest_detail_targets(dict(loan_number="123-1", row_key="DUP"),
            claims, 10, "A", claim_index=detail.DetailClaimIndex(claims))
        self.assertEqual(result, ([], "Fila ID duplicada"))

    def test_fifo_uses_updated_reservations_without_caching_assignments(self):
        claims = [claim("early"), claim("late", fifo_applications=[
            dict(id="late", event_date="2025-02-15", amount_usd=10)])]
        index, reserved = detail.DetailClaimIndex(claims), {}
        row = dict(loan_number="123-1", client_number="12")
        results = []
        for _ in range(3):
            kwargs = dict(period="P1", apply_fifo=True, reserved_amounts=reserved)
            expected = detail.suggest_detail_targets(row, claims, 12, "A", **kwargs)
            actual = detail.suggest_detail_targets(row, claims, 12, "A", claim_index=index, **kwargs)
            self.assertEqual(actual, expected)
            results.append(sum(target["amount_usd"] for target in actual[0]))
            for target in actual[0]:
                reserved[target["claim_id"]] = reserved.get(target["claim_id"], 0) + target["amount_usd"]
        self.assertEqual(results, [12, 8, 0])

    def test_tolerance_and_positive_complements_preserve_amounts(self):
        row = dict(loan_number="123-1", client_number="12")
        for claims, amount, tolerance in [([claim("app")], 10.01, .01),
                ([claim("app"), claim("comp", id="X:comp", kind="X", amount_usd=2)], 12, 0)]:
            self.assertEqual(
                detail.suggest_detail_targets(row, claims, amount, "A", tolerance_usd=tolerance),
                detail.suggest_detail_targets(row, claims, amount, "A", tolerance_usd=tolerance,
                    claim_index=detail.DetailClaimIndex(claims)))

    def test_large_deposit_checks_only_same_credit_within_selected_scope(self):
        claims = [claim(str(i), loan_number=f"{i + 1}-1") for i in range(570)]
        claims.extend(claim(f"old-{i}", loan_number=f"{i + 1}-1", period="OLD") for i in range(5317))
        index = detail.DetailClaimIndex(claims)
        with patch.object(detail, "_eligible_claims", wraps=detail._eligible_claims) as scoped, \
                patch.object(detail, "_candidate_matches", wraps=detail._candidate_matches) as matches:
            for i in range(570):
                targets, _ = detail.suggest_detail_targets(dict(loan_number=f"000{i + 1}-1"),
                    claims, 10, "A", "P1", apply_fifo=True, claim_index=index)
                self.assertEqual([target["claim_id"] for target in targets], [f"H:{i}"])
        self.assertEqual(scoped.call_count, 1)
        self.assertEqual(matches.call_count, 570)


if __name__ == "__main__":
    unittest.main()
