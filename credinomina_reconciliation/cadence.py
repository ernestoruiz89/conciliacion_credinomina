"""Collection cycles are distinct from the date the company pays."""

from __future__ import annotations

from calendar import monthrange
from datetime import date

MONTHLY = "Mensual"
FIRST_HALF = "Primera quincena"
SECOND_HALF = "Segunda quincena"


def cycle_for_frequency(frequency: str, selected: str | None) -> str:
    frequency = frequency or MONTHLY
    selected = selected or ""
    if frequency == MONTHLY:
        if selected not in ("", MONTHLY):
            raise ValueError("Una empresa mensual solo admite el ciclo Mensual.")
        return MONTHLY
    if frequency == "Quincenal":
        if selected not in (FIRST_HALF, SECOND_HALF):
            raise ValueError("Seleccione primera o segunda quincena.")
        return selected
    raise ValueError("Frecuencia de cobranza no reconocida.")


def cycle_cutoff(month: date, cycle: str) -> date:
    if cycle == FIRST_HALF:
        return date(month.year, month.month, 15)
    if cycle in (MONTHLY, SECOND_HALF):
        return date(month.year, month.month, monthrange(month.year, month.month)[1])
    raise ValueError("Ciclo de cobranza no reconocido.")


def cycle_code(cycle: str) -> str:
    return {
        MONTHLY: "M",
        FIRST_HALF: "Q1",
        SECOND_HALF: "Q2",
    }[cycle]


def cycles_conflict(existing: str | None, proposed: str | None) -> bool:
    """A month is monthly OR split into Q1 and Q2, never both."""
    if not existing or not proposed:
        return True  # Historical periods have no payroll cycle.
    return existing == proposed or MONTHLY in (existing, proposed)


def unique_full_quincena_pair(candidates: list[dict], amount_usd: float) -> list[dict]:
    """Return the sole Q1/Q2 pair whose remaining balances equal one core payment.

    Candidates have already passed the loan, client, installment and reference
    checks. We never invent a split for an amount smaller than the two balances.
    """
    matches = []
    for first in candidates:
        if first["cycle"] != FIRST_HALF or first["available_usd"] <= 0.01:
            continue
        for second in candidates:
            if second["cycle"] != SECOND_HALF or second["available_usd"] <= 0.01:
                continue
            if (first["employer"], first["month"]) != (
                second["employer"], second["month"]
            ):
                continue
            if abs(
                round(first["available_usd"] + second["available_usd"], 4)
                - round(amount_usd, 4)
            ) <= 0.0001:
                matches.append([first, second])
    return matches[0] if len(matches) == 1 else []
