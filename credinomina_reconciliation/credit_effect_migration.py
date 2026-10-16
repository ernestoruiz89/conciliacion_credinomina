"""Preserve legacy journals, linking only uniquely documented management evidence."""
import copy
import re
import unicodedata

import frappe
from frappe.utils import getdate

from credinomina_reconciliation import client_credit, credit_usage
from credinomina_reconciliation.rounding import money, sum_money


def _text(value):
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.findall(r'[a-z0-9]+', value))


def _evidence_matches(entry, rows):
    """Amount alone, a name or a loan number never establish the relationship."""
    reason = _text(' '.join(row.get('reason') or '' for row in rows))
    reference = _text(entry.get('referencia'))
    if len(reference) >= 4 and re.search(
            r'\b(?:ref(?:erencia)?|comprobante|documento|asiento)(?:\s+(?:n|no|numero))?\s+' + re.escape(reference) + r'\b', reason):
        return True
    parts = re.split(r'concepto\s*:', entry.get('observaciones') or '', flags=re.I)
    concept = _text(parts[-1].strip().splitlines()[0]) if len(parts) > 1 and parts[-1].strip() else ''
    if len(concept) < 30 or concept not in reason or not entry.get('fecha'):
        return False
    date = getdate(entry['fecha'])
    return any(_text(value) in reason for value in (date.strftime('%d/%m/%Y'), str(date)))


def proposed_links(item):
    from credinomina_reconciliation.complementary_history import _rows, _group
    history = credit_usage.history_rows(item)
    rows = _rows(item, 'compensation')
    # Only old rows without an explicit decision may be inferred.
    if not rows or any(not row.get('operation_id') for row in rows):
        return {}
    proposals, seen = {}, set()
    for row in rows:
        if row['operation_id'] in seen:
            continue
        group = _group(rows, 'compensation', row['operation_id'])
        seen.update(entry['operation_id'] for entry in group)
        if any(entry.get('credit_effect') for entry in group):
            continue
        amount = sum_money(entry.get('amount_usd') for entry in group)
        if amount <= 0:
            continue
        candidates = []
        for index, entry in enumerate(history):
            if entry.get('reverses') or entry.get('application_id'):
                continue
            entry_id = client_credit.management_entry_id(entry, index)
            managed = money(entry.get('importe_usd')) + sum_money(
                other.get('importe_usd') for other in history if other.get('reverses') == entry_id)
            if managed == amount and _evidence_matches(entry, group):
                candidates.append(entry_id)
        if len(candidates) == 1:
            metadata = dict(credit_effect=credit_usage.DOCUMENTARY, credit_management_item=item.name,
                            credit_management_entry=candidates[0])
            proposals.update({entry['operation_id']: metadata for entry in group})
    return proposals


def execute():
    # The old patch may still be pending; both patch entry points use this logic.
    if not frappe.get_meta('CN Complementary Offset').has_field('credit_effect'):
        frappe.reload_doc('conciliacion_credinomina', 'doctype', 'cn_complementary_offset')
    if not frappe.get_meta('CN Complementary Item').has_field('credit_usage_issue'):
        frappe.reload_doc('conciliacion_credinomina', 'doctype', 'cn_complementary_item')
    for name in frappe.get_all('CN Complementary Item', filters={
            'docstatus': 1, 'category': ['in', client_credit.CREDIT_CATEGORIES], 'result': client_credit.RESULT},
            pluck='name', order_by='name', limit_page_length=0):
        item = frappe.get_doc('CN Complementary Item', name, for_update=True)
        updates = []
        try:
            proposals = proposed_links(item)
            projected = copy.deepcopy(item)
            for row in projected.get('compensations') or []:
                metadata = proposals.get(row.get('operation_id'))
                if not metadata:
                    continue
                counterpart = frappe.get_doc('CN Complementary Item', row.get('counterpart'), for_update=True)
                mirrors = [other for other in counterpart.compensations if other.operation_id == row.operation_id]
                if (len(mirrors) != 1 or mirrors[0].counterpart != item.name
                        or money(mirrors[0].amount_usd) != money(row.amount_usd)
                        or getdate(mirrors[0].compensation_date) != getdate(row.compensation_date)
                        or any((mirrors[0].get(field) or '') != (row.get(field) or '')
                               for field in (*credit_usage.EFFECT_FIELDS, 'reverses_operation_id', 'corrects_operation_id'))):
                    raise ValueError('La compensación no coincide con su contrapartida.')
                from credinomina_reconciliation.complementary_compensation import _validate_credit_evidence
                _validate_credit_evidence(item, counterpart)
                row.update(metadata)
                updates.extend([(row.name, item.name, metadata), (mirrors[0].name, counterpart.name, metadata)])
            values = credit_usage.totals(projected, allow_review=True)
        except (frappe.ValidationError, ValueError) as exc:
            # Preserve both histories and block new uses rather than guessing an
            # association, erasing evidence, or preventing the whole site upgrade.
            issue = 'Revise el tratamiento de las compensaciones y su gestión vinculada. ' + str(exc)
            frappe.db.set_value('CN Complementary Item', name, dict(credit_usage_issue=issue,
                credit_pending_usd=0, credit_management_status='Revisar vínculo'))
            frappe.clear_document_cache('CN Complementary Item', name)
            frappe.log_error(title='Saldo a favor: revisar vínculo contable', message=f'{name}: {issue}')
            continue
        for row_name, parent, metadata in updates:
            frappe.db.set_value('CN Complementary Offset', row_name, metadata)
            frappe.clear_document_cache('CN Complementary Item', parent)
        if updates:
            for parent in sorted({parent for row_name, parent, metadata in updates}):
                frappe.get_doc('CN Complementary Item', parent).add_comment('Info',
                    f'Migración: vínculo contable de gestión identificada en {name}. Se conservaron importes, fechas y ambos historiales.')
        frappe.db.set_value('CN Complementary Item', name, dict(values, credit_usage_issue=''))
        frappe.clear_document_cache('CN Complementary Item', name)
