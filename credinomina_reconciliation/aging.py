"""Informational payroll differences, separate from application receivables."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from credinomina_reconciliation.rounding import money, money_float


BUCKETS = ("not_due", "days_1_15", "days_16_30", "days_31_60", "days_61_90", "days_over_90")
VALID_DEDUCTION_STATUSES = (
    "Deduccion total", "Deduccion parcial", "No deducido",
    "Deduccion en exceso", "Inferida por depósito",
)


def deduction_pending_type(row: Mapping[str, Any]) -> str | None:
    """Compatibility name: legacy employer deductions never gate the workflow."""
    return None


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def age_balance(amount: float, due_date: Any, as_of_date: Any) -> dict[str, Any]:
    """Put one positive USD balance into exactly one non-overlapping band."""
    unknown = amount is None
    amount = money_float(max(money(amount), 0)) if not unknown else None
    due = _date(due_date)
    as_of = _date(as_of_date)
    if as_of is None:
        raise ValueError("Indique una fecha para calcular la antigüedad.")
    days = max((as_of - due).days, 0) if due else None
    bucket = (
        "without_date" if due is None else
        "not_due" if days == 0 else
        "days_1_15" if days <= 15 else
        "days_16_30" if days <= 30 else
        "days_31_60" if days <= 60 else
        "days_61_90" if days <= 90 else "days_over_90"
    )
    values = {name: None if unknown else 0.0 for name in (*BUCKETS, "without_date")}
    values[bucket] = amount
    return {"age_days": days, "age_bucket": bucket, **values}


def collection_shortfall_usd(row: Mapping[str, Any]) -> float | None:
    """Absolute collection/application quality gap, never a receivable."""
    from credinomina_reconciliation.application_quality import collection_quality
    result = collection_quality(row)
    difference = result["quality_difference_usd"]
    return None if difference is None else money_float(abs(money(difference)))


def unassigned_deduction_usd(row: Mapping[str, Any]) -> float:
    """Deprecated wire name; use actual application coverage, not deductions."""
    from credinomina_reconciliation.application_quality import application_cash_covered
    return money_float(max(money(row.get("applied_usd")) - money(application_cash_covered(row)), 0))


def operational_balances(row: Mapping[str, Any], period: Mapping[str, Any]):
    """Informational quality findings only; receivables come from application_aging."""
    gap = collection_shortfall_usd(row)
    if gap is None or gap > 0:
        yield {"balance_type": "Cobranza sin conversión US$" if gap is None else "Diferencia cobranza vs aplicación (informativo)",
               "amount_usd": gap or 0, "due_date": period.get("cutoff_date"), "provision_review_usd": 0}
