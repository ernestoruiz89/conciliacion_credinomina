"""Read-only financial presentation of periods, independent of quality control."""
from credinomina_reconciliation.rounding import money, money_float


def financial_position(period):
    applied = period.get('applied_total_usd')
    covered = period.get('remitted_total_usd')
    pending = period.get('pending_usd')
    # Fallback is for snapshots predating the totals fields, not for stored zero.
    applied = money(period.get('applied_usd') if applied is None else applied)
    covered = money(period.get('remitted_usd')) - money(period.get('rounding_adjustment_usd')) if covered is None else money(covered)
    pending = max(applied - covered, money(0)) if pending is None else money(pending)
    status = period.get('status_before_close') if period.get('status') == 'Cerrado' else period.get('status')
    if covered < 0 or covered > applied or status == 'Con excedente':
        state = 'excedente'
    elif status == 'Parcial':
        state = 'parcial'
    elif pending <= 0 and (applied > 0 or status == 'Conciliado'):
        state = 'conciliado'
    elif covered > 0:
        state = 'parcial'
    else:
        state = 'en_transito'
    return {'financial_applied_usd': money_float(applied), 'financial_covered_usd': money_float(covered),
            'financial_pending_usd': money_float(pending), 'control_state': state}
