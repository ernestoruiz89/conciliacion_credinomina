"""Informational payroll differences, separate from application receivables."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from credinomina_reconciliation.rounding import money, money_float


BUCKETS = ("not_due", "days_1_30", "days_31_60", "days_61_90", "days_over_90")
VALID_DEDUCTION_STATUSES = (
    "Deduccion total", "Deduccion parcial", "No deducido",
    "Deduccion en exceso", "Inferida por depósito",
)


def deduction_pending_type(row: Mapping[str, Any]) -> str | None:
    """Unknown/invalid deductions are evidence to review, never confirmed debt."""
    status = row.get("deduction_status")
    if not status or status == "Pendiente de detalle":
        return "Detalle de empresa pendiente"
    if status not in VALID_DEDUCTION_STATUSES:
        return "Detalle de empresa por aclarar"
    return None


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def age_balance(amount: float, due_date: Any, as_of_date: Any) -> dict[str, Any]:
    """Put one positive USD balance into exactly one non-overlapping band."""
    amount = money_float(max(money(amount), 0))
    due = _date(due_date)
    as_of = _date(as_of_date)
    if as_of is None:
        raise ValueError("Indique una fecha para calcular la antigüedad.")
    days = max((as_of - due).days, 0) if due else None
    bucket = (
        "without_date" if due is None else
        "not_due" if days == 0 else
        "days_1_30" if days <= 30 else
        "days_31_60" if days <= 60 else
        "days_61_90" if days <= 90 else "days_over_90"
    )
    values = {name: 0.0 for name in (*BUCKETS, "without_date")}
    values[bucket] = amount
    return {"age_days": days, "age_bucket": bucket, **values}


def collection_shortfall_usd(row: Mapping[str, Any]) -> float | None:
    """Requested but not deducted; this is not an account receivable."""
    if deduction_pending_type(row):
        return None
    return money_float(max(
        money(row.get("expected_usd")) - money(row.get("deducted_usd")), 0
    ))


def unassigned_deduction_usd(row: Mapping[str, Any]) -> float | None:
    if deduction_pending_type(row):
        return None
    return money_float(max(money(row.get("deducted_usd")) - money(row.get("remitted_usd"))
        - max(money(row.get("fx_variance_usd")), 0)
        - max(-money(row.get("rounding_adjustment_usd")), 0), 0))


def operational_balances(row: Mapping[str, Any], period: Mapping[str, Any]):
    """First-reconciliation diagnostics only; not financial receivables."""
    expected = money(row.get("expected_usd"))
    worker_shortfall = collection_shortfall_usd(row)
    if worker_shortfall is None:
        if expected > 0:
            yield {
                "balance_type": deduction_pending_type(row),
                "amount_usd": money_float(expected),
                "due_date": period.get("cutoff_date"),
                "provision_review_usd": 0,
            }
        return
    if worker_shortfall > 0:
        yield {
            "balance_type": "Cobranza no deducida (informativo)",
            "amount_usd": worker_shortfall,
            "due_date": period.get("cutoff_date"),
            "provision_review_usd": 0,
        }
    unassigned_deduction = unassigned_deduction_usd(row)
    if unassigned_deduction > 0:
        yield {
            "balance_type": "Deducido sin depósito asignado",
            "amount_usd": unassigned_deduction,
            "due_date": period.get("remittance_due_date"),
            "provision_review_usd": 0,
        }
