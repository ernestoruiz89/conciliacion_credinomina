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

CONSUME = 'Consume saldo'
DOCUMENTARY = 'Vínculo de gestión'
EFFECT_FIELDS = ('credit_effect', 'credit_management_item', 'credit_management_entry')


def consuming_offsets(offsets):
    return [row for row in offsets if row.get('credit_effect') != DOCUMENTARY]


def validate_links(history, offsets, item_name=None):
    from credinomina_reconciliation.client_credit import management_entry_id
    entries = {management_entry_id(row, index): row for index, row in enumerate(history)}
    linked = defaultdict(list)
    for row in offsets:
        effect = row.get('credit_effect') or CONSUME
        if effect not in (CONSUME, DOCUMENTARY):
            frappe.throw(_('Revise el tratamiento de la compensación.'))
        if effect == DOCUMENTARY:
            if item_name and row.get('credit_management_item') != item_name:
                frappe.throw(_('El vínculo contable debe identificar la partida del saldo a favor.'))
            linked[row.get('credit_management_entry')].append(row)
        elif row.get('credit_management_entry') or row.get('credit_management_item'):
            frappe.throw(_('Una compensación que consume saldo no puede identificar otra gestión.'))
    for entry_id, rows in linked.items():
        original = entries.get(entry_id)
        if not original or original.get('reverses') or money(original.get('importe_usd')) <= 0:
            frappe.throw(_('La gestión vinculada no existe. Retire o reclasifique primero su vínculo contable.'))
        daily = defaultdict(lambda: [money(0), money(0)])
        for key, row in entries.items():
            if key == entry_id or row.get('reverses') == entry_id:
                daily[str(getdate(row['fecha'])) if row.get('fecha') else ''][0] += money(row.get('importe_usd'))
        for row in rows:
            daily[str(getdate(row.get('compensation_date'))) if row.get('compensation_date') else ''][1] += money(row.get('amount_usd'))
        managed, assigned = money(0), money(0)
        for day in sorted(daily):
            managed += daily[day][0]
            assigned += daily[day][1]
            if assigned < 0 or assigned > managed:
                frappe.throw(_('El vínculo contable excede la gestión disponible al {0}. Revise la fecha, otros vínculos o la reversión de la gestión.').format(day))


def history_rows(item):
    value = item.get('credit_history') or []
    return json.loads(value) if isinstance(value, str) else value


def resolved_amount(item, history=None):
    history = history_rows(item) if history is None else history
    return (sum_money(row.get('importe_usd') for row in history)
            + sum_money(row.get('amount_usd') for row in consuming_offsets(item.get('compensations') or [])))


def validate_history(original, history, offsets=()):
    validate_links(history, offsets)
    by_day = defaultdict(lambda: money(0))
    for rows, date_field, amount_field in ((history, 'fecha', 'importe_usd'),
                                            (consuming_offsets(offsets), 'compensation_date', 'amount_usd')):
        for row in rows:
            # Undated legacy management stays reserved before dated operations.
            day = str(getdate(row.get(date_field))) if row.get(date_field) else ''
            by_day[day] += money(row.get(amount_field))
    used = money(0)
    for day in sorted(by_day):
        used += by_day[day]
        if used < 0 or used > money(original):
            frappe.throw(_('La fecha dejaría un saldo a favor inválido al {0}. Revise las aplicaciones, compensaciones y reversiones previas.').format(day))


def totals(item, history=None, offsets=None, allow_review=False):
    if item.get('credit_usage_issue') and not allow_review and history is None and offsets is None:
        frappe.throw(_('Revise el tratamiento de las compensaciones antes de utilizar este saldo a favor.'))
    history = history_rows(item) if history is None else history
    offsets = (item.get('compensations') or []) if offsets is None else offsets
    validate_links(history, offsets, item.get('name'))
    validate_history(item.get('amount_usd'), history, offsets)
    used = sum_money(row.get('importe_usd') for row in history) + sum_money(row.get('amount_usd') for row in consuming_offsets(offsets))
    pending = money(item.get('amount_usd')) - used
    return dict(credit_resolved_usd=money_float(used), credit_pending_usd=money_float(pending),
                credit_management_status='Resuelto' if not pending else 'Parcialmente resuelto' if used else 'Pendiente')
