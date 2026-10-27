"""Small, auditable USD reconciliation differences; never a loan/core posting."""

from __future__ import annotations

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
    """Retired: differences remain visible and must be documented, never absorbed."""
    return []
