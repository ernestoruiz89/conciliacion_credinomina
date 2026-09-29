"""Small, auditable USD reconciliation differences; never a loan/core posting."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, ROUND_HALF_UP

CENT_PRECISION = Decimal("0.01")
RATE_PRECISION = Decimal("0.00000001")
MONEY_EPSILON = Decimal("0.005")
CASH_EPSILON = 0.005


def decimal_value(value) -> Decimal:
    """Convert through text, never through the binary float representation."""
    if isinstance(value, Decimal):
        result = value
    else:
        result = Decimal(str(value or 0))
    return result if result.is_finite() else Decimal(0)


def money(value) -> Decimal:
    return decimal_value(value).quantize(CENT_PRECISION, rounding=ROUND_HALF_UP)


def money_float(value) -> float:
    """Return a float only at interfaces that cannot accept Decimal values."""
    return float(money(value))


def sum_money(values) -> Decimal:
    return sum((money(value) for value in values), Decimal(0)).quantize(
        CENT_PRECISION, rounding=ROUND_HALF_UP
    )


def _round_document_fields(document):
    meta = getattr(document, "meta", None)
    if not meta:
        return
    for field in meta.fields:
        if field.fieldtype == "Table":
            for row in document.get(field.fieldname) or ():
                _round_document_fields(row)
            continue
        is_money = field.fieldtype == "Currency" or (
            field.fieldtype == "Float" and str(field.precision or "") == "2"
        )
        if not is_money:
            continue
        value = document.get(field.fieldname)
        if value not in (None, ""):
            document.set(field.fieldname, money_float(value))


def round_document_money(document, method=None):
    """Round monetary DocFields and their child rows before persistence.

    Exchange rates and other high-precision Float fields are left untouched;
    monetary Float source fields are explicitly declared with precision 2.
    """
    _round_document_fields(document)


def rounding_movements(
    deposits: Iterable[Mapping],
    claims: Iterable[Mapping],
    allocations: Iterable[Mapping],
    deposit_remaining: Mapping,
    claim_remaining: Mapping,
    tolerance_by_employer: Mapping,
    blocked_deposit_ids: Iterable[str] = (),
) -> list[dict]:
    """Bridge a unique one-deposit/one-application USD difference within policy.

    The claim must already have a real cash allocation. Partial or many-to-many
    cases, FX, fee claims and ambiguous references are intentionally excluded.
    """
    deposits = {str(row["id"]): row for row in deposits}
    claims = {str(row["id"]): row for row in claims}
    by_deposit = defaultdict(list)
    by_claim = defaultdict(list)
    for entry in allocations:
        if money(entry["amount_usd"]) <= MONEY_EPSILON:
            continue
        by_deposit[str(entry["deposit_id"])].append(entry)
        by_claim[str(entry["claim_id"])].append(entry)
    blocked = {str(value) for value in blocked_deposit_ids}
    movements = []
    for deposit_id, deposit in deposits.items():
        entries = by_deposit[deposit_id]
        if deposit_id in blocked or len(entries) != 1:
            continue
        entry = entries[0]
        claim_id = str(entry["claim_id"])
        claim = claims.get(claim_id)
        if (
            not claim or not claim_id.startswith(("C:", "H:"))
            or len(by_claim[claim_id]) != 1
            or deposit.get("currency") != "USD"
            or deposit.get("bank_currency") != "USD"
            or money(deposit.get("bank_amount_usd")) != money(deposit["amount_usd"])
        ):
            continue
        application_ids = list(claim.get("application_ids") or ())
        if len(application_ids) != 1:
            continue
        employer = claim.get("group")
        if deposit.get("group") and deposit["group"] != employer:
            continue
        tolerance = money(tolerance_by_employer.get(employer))
        if tolerance <= MONEY_EPSILON:
            continue
        core_amount = money(claim.get("core_applied_usd"))
        claim_amount = money(claim["amount_usd"])
        cash_amount = money(deposit["amount_usd"])
        delta = cash_amount - core_amount
        if (
            core_amount <= MONEY_EPSILON
            or abs(delta) <= MONEY_EPSILON
            or abs(delta) > tolerance
            or abs(cash_amount - claim_amount) > tolerance
            or abs(core_amount - claim_amount) > tolerance
            or money(deposit_remaining.get(deposit_id)) > tolerance
            or money(claim_remaining.get(claim_id)) > tolerance
        ):
            continue
        consumed = min(max(delta, Decimal(0)), money(deposit_remaining.get(deposit_id)))
        key_source = (
            f"{deposit_id}|{claim_id}|{application_ids[0]}|"
            f"{delta:.4f}|{claim_amount:.4f}|{consumed:.4f}|{tolerance:.4f}|"
            f"{deposit.get('reference') or ''}"
        )
        key = "CN-RND-" + hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:24]
        movements.append({
            "name": key,
            "deposit_id": deposit_id,
            "claim_id": claim_id,
            "application_id": application_ids[0],
            "employer": employer,
            "period": claim.get("period"),
            "signed_amount_usd": float(delta),
            "consumed_residual_usd": float(consumed),
            "tolerance_usd": float(tolerance),
            "core_applied_usd": float(core_amount),
            "deposit_usd": float(cash_amount),
            "claim_usd": float(claim_amount),
        })
    return movements
