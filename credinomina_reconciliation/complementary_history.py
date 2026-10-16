"""Delete a mistaken operation and its dependent entries, recalculating both sides."""
import hashlib
import json
from html import escape

import frappe
from frappe import _
from frappe.utils import getdate

from credinomina_reconciliation import client_credit, complementary_compensation as offsets, credit_application, credit_usage
from credinomina_reconciliation.rounding import money, money_float, sum_money


def _check(item):
    for permission in ('read', 'write', 'submit'):
        item.check_permission(permission)
    if item.docstatus == 2:
        frappe.throw(_('No se puede modificar el historial de una partida cancelada.'))


def _rows(item, kind):
    if kind == 'compensation':
        return [dict(row if isinstance(row, dict) else row.as_dict()) for row in item.get('compensations') or []]
    if kind != 'management':
        frappe.throw(_('Seleccione historial de compensaciones o de gestión.'))
    return [dict(row, entry_id=client_credit.management_entry_id(row, index))
            for index, row in enumerate(credit_usage.history_rows(item))]


def _group(rows, kind, entry_id):
    key = 'operation_id' if kind == 'compensation' else 'entry_id'
    links = ('reverses_operation_id', 'corrects_operation_id') if kind == 'compensation' else ('reverses',)
    ids = [row.get(key) for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        frappe.throw(_('El historial contiene identificadores ausentes o duplicados; revise sus operaciones.'))
    if entry_id not in ids:
        frappe.throw(_('La operación ya no existe. Recargue el historial.'))
    selected = {entry_id}
    while True:
        expanded = set(selected)
        for row in rows:
            family = {row[key], *(row[field] for field in links if row.get(field))}
            if family & selected:
                expanded.update(family)
        if expanded == selected:
            break
        selected = expanded
    if selected - set(ids):
        frappe.throw(_('Falta una operación relacionada con esta reversión o corrección. Revise el historial.'))
    return [row for row in rows if row[key] in selected]


@frappe.whitelist()
def deletion_options(item_name, kind):
    item = frappe.get_doc(offsets.DOCTYPE, item_name)
    _check(item)
    rows = _rows(item, kind)
    seen, choices = set(), []
    key = 'operation_id' if kind == 'compensation' else 'entry_id'
    for row in rows:
        if row[key] in seen:
            continue
        group = _group(rows, kind, row[key])
        seen.update(entry[key] for entry in group)
        choices.append(dict(entry_id=row[key], date=str(row.get('compensation_date') or row.get('fecha') or ''),
            description=row.get('counterpart') or row.get('tratamiento') or '', count=len(group),
            amount_usd=money_float(sum_money(entry.get('amount_usd' if kind == 'compensation' else 'importe_usd') for entry in group))))
    return dict(rows=choices)


def _prepare(item_name, kind, entry_id):
    initial = frappe.get_doc(offsets.DOCTYPE, item_name)
    _check(initial)
    selected = _group(_rows(initial, kind), kind, entry_id)
    period = None
    if kind == 'compensation':
        counterparts = {row.get('counterpart') for row in selected}
        if len(counterparts) != 1 or not all(counterparts):
            frappe.throw(_('Las operaciones relacionadas deben tener la misma contrapartida.'))
        left, right = offsets._load_pair(item_name, counterparts.pop(), 'submit')
        for doc in (left, right):
            _check(doc)
        offsets._validate_pair(left, right)
        # Recovery receipts have a separate origin and settlement journal.
        if left.get('receivable_origin') or right.get('receivable_origin'):
            frappe.throw(_('Corrija este cobro desde la gestión de recuperación de CxC.'))
        docs = [left, right]
    else:
        application_ids = {row.get('application_id') for row in selected if row.get('application_id')}
        if len(application_ids) > 1:
            frappe.throw(_('La operación contiene aplicaciones diferentes; revise sus vínculos.'))
        if application_ids:
            item, application, period = credit_application._locked_context(item_name, initial.modified, application_ids.pop())
        else:
            item = frappe.get_doc(offsets.DOCTYPE, item_name, for_update=True)
            credit_application._check_item(item)
        docs = [item]
    groups = [_group(_rows(doc, kind), kind, entry_id) for doc in docs]
    if kind == 'compensation':
        def signature(rows):
            return sorted((row['operation_id'], str(getdate(row['compensation_date'])), str(money(row['amount_usd'])),
                           row.get('reverses_operation_id') or '', row.get('corrects_operation_id') or '',
                           *(row.get(field) or '' for field in credit_usage.EFFECT_FIELDS)) for row in rows)
        if signature(groups[0]) != signature(groups[1]) or any(
                row.get('counterpart') != other.name for group, other in zip(groups, reversed(docs)) for row in group):
            frappe.throw(_('Las dos partes del historial no coinciden; no se eliminó ninguna operación.'))
    key = 'operation_id' if kind == 'compensation' else 'entry_id'
    remaining, changes = [], []
    for doc, group in zip(docs, groups):
        ids = {row[key] for row in group}
        rest = [row for row in _rows(doc, kind) if row[key] not in ids]
        if kind == 'management':
            # entry_id is a presentation-only ID, including for legacy rows.
            rest = [dict({field: value for field, value in row.items() if field != 'entry_id'},
                         operation_id=row.get('operation_id') or row['entry_id']) for row in rest]
            values = credit_usage.totals(doc, history=rest)
        else:
            projected = frappe._dict(doc if isinstance(doc, dict) else doc.as_dict())
            projected.compensations = [frappe._dict(row) for row in rest]
            offsets._validate_correction_timeline(projected, [])
            values = dict(compensated_usd=offsets.balance(projected)['compensated_usd'])
            if doc.category in client_credit.CREDIT_CATEGORIES:
                values.update(credit_usage.totals(projected, allow_review=True))
        remaining.append(rest)
        changes.append(dict(name=doc.name, removed_count=len(group), **values))
    snapshot = [dict(name=doc.name, modified=str(doc.modified), history=_rows(doc, kind),
                     credit_history=doc.get('credit_history'), offsets=_rows(doc, 'compensation')) for doc in docs]
    token = hashlib.sha256(json.dumps([kind, entry_id, snapshot], sort_keys=True, default=str).encode()).hexdigest()
    return docs, groups, remaining, period, dict(token=token, changes=changes)


@frappe.whitelist()
def preview_deletion(item_name, kind, entry_id):
    return _prepare(item_name, kind, entry_id)[-1]


@frappe.whitelist(methods=['POST'])
def delete_history(item_name, kind, entry_id, token, reason):
    reason = (reason or '').strip()
    if not reason:
        frappe.throw(_('Indique el motivo para eliminar la operación del historial.'))
    docs, groups, remaining, period, preview = _prepare(item_name, kind, entry_id)
    if not token or token != preview['token']:
        frappe.throw(_('La partida o su contrapartida cambió. Revise nuevamente los saldos antes de eliminar.'))
    frappe.db.savepoint('delete_complementary_history')
    try:
        for doc, group, rest in zip(docs, groups, remaining):
            if kind == 'compensation':
                doc.set('compensations', rest)
                doc.flags.compensation_write_token = offsets._WRITE_TOKEN
                try:
                    doc.save()
                finally:
                    doc.flags.pop('compensation_write_token', None)
            else:
                values = credit_usage.totals(doc, history=rest)
                frappe.db.set_value(doc.doctype, doc.name, dict(credit_history=json.dumps(rest, ensure_ascii=False), credit_usage_issue='', **values))
                doc.clear_cache()
            # Keep an audit record outside the financial histories. No compensating
            # entry is generated, and the deleted amount no longer affects cutoffs.
            audit = json.dumps(group, ensure_ascii=False, default=str)
            doc.add_comment('Info', _('Operación eliminada del historial ({0}). Motivo: {1}').format(escape(kind), escape(reason))
                            + '<pre>' + escape(audit) + '</pre>')
        if period:
            credit_application.rebuild_destination(period)
    except Exception:
        frappe.db.rollback(save_point='delete_complementary_history')
        raise
    return preview
