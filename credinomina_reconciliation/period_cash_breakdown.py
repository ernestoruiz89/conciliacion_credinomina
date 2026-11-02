"""Separate signed deposit offsets without assigning them to individual loans.

Inputs contain the complete executed journal for every relevant deposit. Claim
IDs use the engine's H:/C:/X: convention; claim_periods resolves H: and C: to
their current period. No database access or accounting mutations occur here.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from credinomina_reconciliation.rounding import money, money_float, sum_money


def _unknown(reason):
    return dict(known=False, cash_adjustment_usd=None,
                negative_complementary_usd=None, reason=reason)


def calculate(period_name, allocations, claim_periods):
    """Return the signed correction to an existing gross application total.

    A bank deposit of 90 executing H:100 and X:-10 has a cash correction of
    -10 and a signed complementary amount of -10. If every positive portion
    belongs to one period, this is exact for that period, including when its
    applications are shared. No per-application correction is inferred.

    A negative portion shared across periods, or with a positive non-loan
    destination, cannot be assigned to the requested period by assumption.
    The caller must supply complete journals, not just the requested period's
    entries, so the result cannot hide another beneficiary of the same cash.
    """
    grouped = defaultdict(list)
    try:
        for entry in allocations:
            amount = Decimal(str(entry.get('amount_usd')))
            if not amount.is_finite() or not entry.get('deposit_id'):
                raise ValueError()
            grouped[entry['deposit_id']].append((str(entry.get('claim_id') or ''), money(amount)))
    except (TypeError, ValueError, InvalidOperation, AttributeError):
        return _unknown('La distribución ejecutada contiene importes o identidades de depósito inválidos.')

    adjustment = money(0)
    for portions in grouped.values():
        negatives = [(claim, amount) for claim, amount in portions if amount < 0]
        if not negatives:
            continue
        loans = [(claim, amount) for claim, amount in portions
                 if amount > 0 and claim.partition(':')[0] in {'H', 'C'}]
        periods = {claim_periods.get(claim) for claim, _amount in loans}
        # A fully identified deposit paid another period. Its signed counterpart
        # must not be repeated in every period consulted.
        if loans and None not in periods and '' not in periods and period_name not in periods:
            continue
        if not loans:
            if all(claim.startswith('X:') for claim, _amount in negatives):
                continue  # Complement-only activity is not application coverage.
            return _unknown('La distribución contiene una aplicación con importe negativo.')
        if any(not claim.startswith('X:') for claim, _amount in negatives):
            return _unknown('La distribución contiene una aplicación con importe negativo.')
        if any(not claim_periods.get(claim) for claim, _amount in loans):
            return _unknown('Falta identificar el período de una aplicación o cobranza del depósito compartido.')
        if periods != {period_name}:
            return _unknown('La complementaria negativa pertenece a un depósito compartido entre varios períodos; falta atribuir su efecto.')
        if any(amount > 0 and claim.partition(':')[0] not in {'H', 'C'} for claim, amount in portions):
            return _unknown('El depósito también cubre otros destinos; falta atribuir la complementaria negativa a las aplicaciones.')
        negative = sum_money(amount for _claim, amount in negatives)
        if sum_money(amount for _claim, amount in loans) + negative < 0:
            return _unknown('Las complementarias negativas superan la cobertura positiva del depósito.')
        adjustment += negative

    return dict(known=True, cash_adjustment_usd=money_float(adjustment),
                negative_complementary_usd=money_float(adjustment), reason='')
