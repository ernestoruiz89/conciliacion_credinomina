"""Explicit relationship between an accounting offset and a recorded credit use."""
import hashlib
import json
from html import escape

import frappe
from frappe import _

from credinomina_reconciliation import client_credit, credit_usage
from credinomina_reconciliation.rounding import money, money_float, sum_money


def management_options(credit):
    rows = credit_usage.history_rows(credit)
    result = []
    for index, entry in enumerate(rows):
        if entry.get('reverses') or money(entry.get('importe_usd')) <= 0:
            continue
        entry_id = client_credit.management_entry_id(entry, index)
        managed = money(entry['importe_usd']) + sum_money(row.get('importe_usd') for row in rows if row.get('reverses') == entry_id)
        linked = sum_money(row.get('amount_usd') for row in credit.get('compensations') or []
                           if row.get('credit_effect') == credit_usage.DOCUMENTARY and row.get('credit_management_entry') == entry_id)
        result.append(dict(entry_id=entry_id, treatment=entry.get('tratamiento') or '', date=entry.get('fecha') or '',
                           reference=entry.get('referencia') or '', amount_usd=money_float(managed),
                           available_usd=money_float(managed - linked)))
    return result


@frappe.whitelist()
def options(item_name, counterpart=None):
    from credinomina_reconciliation import complementary_compensation as offsets
    from credinomina_reconciliation.complementary_history import _check, _rows, _group
    docs = [frappe.get_doc(offsets.DOCTYPE, name) for name in dict.fromkeys(filter(None, (item_name, counterpart)))]
    for doc in docs:
        _check(doc)
    credit = next((doc for doc in docs if doc.category in client_credit.CREDIT_CATEGORIES), None)
    if not credit:
        return dict(credit_item=None, management=[], operations=[])
    seen, operations = set(), []
    rows = _rows(credit, 'compensation')
    for row in rows:
        if row['operation_id'] in seen:
            continue
        group = _group(rows, 'compensation', row['operation_id'])
        seen.update(entry['operation_id'] for entry in group)
        operations.append(dict(operation_id=row['operation_id'], counterpart=row['counterpart'],
            amount_usd=money_float(sum_money(entry.get('amount_usd') for entry in group)),
            credit_effect=row.get('credit_effect') or credit_usage.CONSUME,
            credit_management_entry=row.get('credit_management_entry') or ''))
    return dict(credit_item=credit.name, management=management_options(credit), operations=operations)


def _prepare(item_name, operation_id, credit_effect, credit_management_entry):
    from credinomina_reconciliation import complementary_compensation as offsets
    from credinomina_reconciliation.complementary_history import _check, _rows, _group
    initial = frappe.get_doc(offsets.DOCTYPE, item_name)
    _check(initial)
    selected = _group(_rows(initial, 'compensation'), 'compensation', operation_id)
    counterparts = {row['counterpart'] for row in selected}
    if len(counterparts) != 1:
        frappe.throw(_('Las operaciones relacionadas tienen contrapartidas diferentes.'))
    docs = offsets._load_pair(item_name, counterparts.pop(), 'submit')
    for doc in docs:
        _check(doc)
    offsets._validate_pair(*docs)
    credit = next((doc for doc in docs if doc.category in client_credit.CREDIT_CATEGORIES), None)
    if not credit:
        frappe.throw(_('Seleccione la compensación de un saldo a favor.'))
    groups = [_group(_rows(doc, 'compensation'), 'compensation', operation_id) for doc in docs]
    def signature(group):
        return sorted((row['operation_id'], str(row['compensation_date']), str(money(row['amount_usd'])),
                       row.get('reverses_operation_id') or '', row.get('corrects_operation_id') or '',
                       *(row.get(field) or '' for field in credit_usage.EFFECT_FIELDS)) for row in group)
    if signature(groups[0]) != signature(groups[1]) or any(row['counterpart'] != other.name
            for group, other in zip(groups, reversed(docs)) for row in group):
        frappe.throw(_('Las dos partes de la compensación no coinciden.'))
    metadata = offsets._credit_metadata(docs, credit_effect, credit_management_entry)
    fingerprint = json.dumps([operation_id, metadata, [dict(name=doc.name, modified=str(doc.modified),
        history=doc.get('credit_history'), offsets=_rows(doc, 'compensation')) for doc in docs]], sort_keys=True, default=str)
    for doc, group in zip(docs, groups):
        selected_ids = {row['operation_id'] for row in group}
        for row in doc.compensations:
            if row.operation_id in selected_ids:
                row.update(metadata)
    values = credit_usage.totals(credit, allow_review=True)
    return docs, dict(token=hashlib.sha256(fingerprint.encode()).hexdigest(), credit_item=credit.name, **values)


@frappe.whitelist()
def preview(item_name, operation_id, credit_effect, credit_management_entry=None):
    return _prepare(item_name, operation_id, credit_effect, credit_management_entry)[1]


@frappe.whitelist(methods=['POST'])
def classify(item_name, operation_id, credit_effect, token, reason, credit_management_entry=None):
    from credinomina_reconciliation import complementary_compensation as offsets
    if not (reason or '').strip():
        frappe.throw(_('Indique el motivo del tratamiento contable.'))
    docs, result = _prepare(item_name, operation_id, credit_effect, credit_management_entry)
    if result['token'] != token:
        frappe.throw(_('Cambió una de las partidas; revise nuevamente el tratamiento.'))
    frappe.db.savepoint('classify_credit_effect')
    try:
        for doc in docs:
            doc.flags.compensation_write_token = offsets._WRITE_TOKEN
            try:
                doc.save()
            finally:
                doc.flags.pop('compensation_write_token', None)
            doc.add_comment('Info', _('Tratamiento de compensación {0}: {1}. Gestión: {2}. Motivo: {3}').format(
                escape(operation_id), escape(credit_effect), escape(credit_management_entry or ''), escape(reason)))
    except Exception:
        frappe.db.rollback(save_point='classify_credit_effect')
        raise
    return result
