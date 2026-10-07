"""Collection cycles are distinct from the date the company pays."""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from credinomina_reconciliation.rounding import money

MONTHLY = "Mensual"
FIRST_HALF = "Primera quincena"
SECOND_HALF = "Segunda quincena"
EXACT_DATE = "Fecha exacta"


def cycle_for_frequency(frequency: str, selected: str | None) -> str:
    frequency = frequency or MONTHLY
    selected = selected or ""
    if selected == EXACT_DATE and frequency in (MONTHLY, "Quincenal"):
        return EXACT_DATE
    if frequency == MONTHLY:
        if selected not in ("", MONTHLY):
            raise ValueError("Una empresa mensual admite Mensual o Fecha exacta.")
        return MONTHLY
    if frequency == "Quincenal":
        if selected not in (FIRST_HALF, SECOND_HALF):
            raise ValueError("Seleccione primera quincena, segunda quincena o Fecha exacta.")
        return selected
    raise ValueError("Frecuencia de cobranza no reconocida.")


def cycle_cutoff(month: date, cycle: str, exact_date: date | None = None) -> date:
    if cycle == EXACT_DATE:
        if not exact_date:
            raise ValueError("Indique la fecha de corte para el ciclo Fecha exacta.")
        if (exact_date.year, exact_date.month) != (month.year, month.month):
            raise ValueError("La fecha de corte debe pertenecer al mes de cobranza.")
        return exact_date
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
        EXACT_DATE: "FE",
    }[cycle]


def cycles_conflict(existing: str | None, proposed: str | None,
                    existing_date=None, proposed_date=None) -> bool:
    """Extra dated cuts coexist with the regular monthly or fortnightly cycle."""
    if EXACT_DATE in (existing, proposed):
        return existing == proposed and (
            not existing_date or not proposed_date
            or str(existing_date)[:10] == str(proposed_date)[:10]
        )
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
            if money(first["available_usd"]) + money(second["available_usd"]) == money(amount_usd):
                matches.append([first, second])
    return matches[0] if len(matches) == 1 else []
