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
HISTORICAL_MONTHLY = "Mensual"
HISTORICAL_EXACT = "Fecha exacta"
HISTORICAL_RANGE = "Rango de fechas"


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def historical_scope_interval(scope, exact_date=None, start_date=None, end_date=None):
    """Return the inclusive application-date window, or None for monthly periods."""
    scope = scope or HISTORICAL_MONTHLY
    if scope == HISTORICAL_MONTHLY:
        if exact_date or start_date or end_date:
            raise ValueError("Un período histórico mensual no lleva fechas de aplicación.")
        return None
    if scope == HISTORICAL_EXACT:
        if not exact_date or start_date or end_date:
            raise ValueError("Indique solo la fecha exacta de aplicación histórica.")
        day = _as_date(exact_date)
        return day, day
    if scope == HISTORICAL_RANGE:
        if exact_date or not start_date or not end_date:
            raise ValueError("Indique inicio y fin del rango histórico.")
        start, end = _as_date(start_date), _as_date(end_date)
        if start > end:
            raise ValueError("El inicio del rango histórico no puede superar el fin.")
        return start, end
    raise ValueError("Tipo de período histórico no reconocido.")


def historical_scope_contains(scope, event_date, exact_date=None, start_date=None, end_date=None):
    interval = historical_scope_interval(scope, exact_date, start_date, end_date)
    if interval is None:
        return True
    if not event_date:
        return False
    return interval[0] <= _as_date(event_date) <= interval[1]


def historical_scopes_conflict(existing, proposed):
    """Only duplicate monthly buckets or overlapping dated cuts are ambiguous."""
    if existing is None or proposed is None:
        return existing is None and proposed is None
    return existing[0] <= proposed[1] and proposed[0] <= existing[1]


def is_historical_date(value) -> bool:
    if not value:
        return False
    day = _as_date(value)
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
