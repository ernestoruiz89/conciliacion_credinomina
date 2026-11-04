"""Offsets consume the unused cent, never an existing adjustment or payment."""
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import complementary_compensation as offsets
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.complementary_remainders import cash_capacity


def pair():
    base = dict(docstatus=1, posting_date="2025-11-20", accounting_source_key="key", flags=frappe._dict(),
                employer="IMMSA", source_account="160209013004", source_currency="USD",
                client_name="FELIX ANTONIO LAGUNA SANCHEZ", compensations=[])
    return (frappe._dict(base, name="A", category="Ajuste de aplicación", review_action="Ajuste de aplicación",
                amount_usd=1929.35, source_credit=1929.35, source_debit=0, related_application="APP",
                application_adjustment_usd=1929.34, review_status="Ajuste confirmado"),
            frappe._dict(base, name="B", category="Ajuste de conciliación", review_action="Partida de depósito",
                amount_usd=47.28, source_debit=47.28, source_credit=0, review_status="Lista para conciliar"))


def test_only_unconsumed_remainders_are_available_and_review_treatment_survives():
    left, right = pair()
    distributions = {right.name: [dict(deposit="DEP", date="2025-09-02", amount_usd=47.27)]}
    with patch("credinomina_reconciliation.complementary_balances._distribution_index", return_value=distributions), \
            patch.object(frappe, "get_all", return_value=[]):
        offsets._validate_pair(left, right)
        assert offsets._available(left) == offsets._available(right) == offsets.money(.01)
        for doc in (left, right):
            review = doc.review_status
            doc.compensations = [frappe._dict(amount_usd=.01, compensation_date="2025-11-21")]
            offsets.update_totals(doc)
            assert doc.compensated_usd == .01 and doc.compensation_pending_usd == 0
            assert doc.review_status == review
            assert offsets.balance(doc, "2025-10-01")["pending_usd"] == 0
            assert offsets.balance(doc, "2025-11-20")["pending_usd"] == .01
            assert offsets.balance(doc, "2025-11-21")["pending_usd"] == 0
        assert left.application_adjustment_usd == 1929.34 and left.related_application == "APP"
        assert cash_capacity(right) == offsets.money(47.27)
        assert financial_balance(left)["pending_usd"] == 0
        assert financial_balance(right, distributions[right.name])["pending_usd"] == 0
        assert distributions[right.name][0]["amount_usd"] == 47.27


def test_planned_assignments_reserve_the_cent_without_claiming_paid_cash():
    _, right = pair()
    with patch("credinomina_reconciliation.complementary_balances._distribution_index", return_value={
            right.name: [dict(deposit="DEP", date="2025-09-02", amount_usd=47.27)]}), \
            patch.object(frappe, "get_all", return_value=[frappe._dict(parent="DEP", amount_usd=47.28)]):
        assert offsets.balance(right)["pending_usd"] == .01
        assert offsets._available(right) == 0


def test_remainder_pair_rejects_other_client_same_side_and_bad_original_amount():
    for changes in (dict(client_name="OTRO CLIENTE"), dict(source_credit=47.28, source_debit=0),
                    dict(amount_usd=47.29), dict(generic_distribution=1), dict(docstatus=2), dict(source_account="")):
        left, right = pair()
        right.update(changes)
        with patch.object(frappe, "throw", side_effect=ValueError), \
                patch.object(frappe, "db", Mock(get_value=Mock(return_value="Abierto"))):
            try:
                offsets._validate_pair(left, right)
            except ValueError:
                pass
            else:
                raise AssertionError(changes)


def test_invalid_cash_capacity_is_not_clipped_to_zero():
    with patch.object(frappe, "throw", side_effect=ValueError):
        try:
            cash_capacity(dict(name="BAD", amount_usd=47.28, compensated_usd=47.29))
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid compensated balance accepted")
