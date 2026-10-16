"""One dated capacity for credit applications, external management and offsets.

Keep the journals separate: an offset consumes credit but is not loan funding.
Signed entries, including date-correction neutralizers, count on their own dates.
"""
import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import getdate

from credinomina_reconciliation.rounding import money, money_float, sum_money


def history_rows(item):
    value = item.get('credit_history') or []
    return json.loads(value) if isinstance(value, str) else value


def resolved_amount(item, history=None):
    history = history_rows(item) if history is None else history
    return (sum_money(row.get('importe_usd') for row in history)
            + sum_money(row.get('amount_usd') for row in item.get('compensations') or []))


def validate_history(original, history, offsets=()):
    by_day = defaultdict(lambda: money(0))
    for rows, date_field, amount_field in ((history, 'fecha', 'importe_usd'),
                                            (offsets, 'compensation_date', 'amount_usd')):
        for row in rows:
            # Undated legacy management stays reserved before dated operations.
            day = str(getdate(row.get(date_field))) if row.get(date_field) else ''
            by_day[day] += money(row.get(amount_field))
    used = money(0)
    for day in sorted(by_day):
        used += by_day[day]
        if used < 0 or used > money(original):
            frappe.throw(_('La fecha dejaría un saldo a favor inválido al {0}. Revise las aplicaciones, compensaciones y reversiones previas.').format(day))


def totals(item, history=None, offsets=None):
    history = history_rows(item) if history is None else history
    offsets = (item.get('compensations') or []) if offsets is None else offsets
    validate_history(item.get('amount_usd'), history, offsets)
    used = sum_money(row.get('importe_usd') for row in history) + sum_money(row.get('amount_usd') for row in offsets)
    pending = money(item.get('amount_usd')) - used
    return dict(credit_resolved_usd=money_float(used), credit_pending_usd=money_float(pending),
                credit_management_status='Resuelto' if not pending else 'Parcialmente resuelto' if used else 'Pendiente')
