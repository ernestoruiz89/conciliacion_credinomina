"""Rules for the April 2025–August 2026 application/deposit backfill.

Historical evidence does not establish what was sent to payroll or deducted
from an employee. Its balance is therefore an unmatched application, not a
worker shortfall or an employer receivable.
"""

from __future__ import annotations

from datetime import date, datetime
from collections import defaultdict

from credinomina_reconciliation.parsers import clean_text
from credinomina_reconciliation.rounding import CASH_EPSILON

HISTORICAL_START = date(2025, 4, 1)
HISTORICAL_END = date(2026, 8, 31)
OPERATIVE_START = date(2026, 9, 1)


def is_historical_date(value) -> bool:
    if not value:
        return False
    day = (
        value.date() if isinstance(value, datetime)
        else value if isinstance(value, date)
        else date.fromisoformat(str(value)[:10])
    )
    return HISTORICAL_START <= day <= HISTORICAL_END


def historical_balance(applied_usd, remitted_usd) -> float:
    return round(max(float(applied_usd or 0) - float(remitted_usd or 0), 0), 4)


def historical_status(applied_usd, remitted_usd) -> str:
    applied = float(applied_usd or 0)
    remitted = float(remitted_usd or 0)
    if applied <= CASH_EPSILON:
        return "Historico pendiente"
    if historical_balance(applied, remitted) <= CASH_EPSILON:
        return "Historico conciliado"
    if remitted > CASH_EPSILON:
        return "Historico parcial"
    return "Historico pendiente"


def blocked_historical_deposits(claims, deposits) -> set[str]:
    """Require manual allocation when a historical reference is not unique."""
    periods_by_reference = defaultdict(set)
    history_count = defaultdict(int)
    other_references = set()
    deposits_by_reference = defaultdict(list)
    for deposit in deposits:
        deposits_by_reference[clean_text(deposit.get("reference"))].append(str(deposit["id"]))
    for claim in claims:
        for raw_reference in claim.get("references", ()):
            reference = clean_text(raw_reference)
            if not reference:
                continue
            if str(claim["id"]).startswith("H:"):
                periods_by_reference[reference].add(claim.get("period"))
                history_count[reference] += 1
            else:
                other_references.add(reference)
    blocked_references = {
        reference for reference, periods in periods_by_reference.items()
        if len(periods) > 1
        or reference in other_references
        or (history_count[reference] > 1 and len(deposits_by_reference[reference]) > 1)
    }
    return {
        deposit_id for reference in blocked_references
        for deposit_id in deposits_by_reference[reference]
    }
