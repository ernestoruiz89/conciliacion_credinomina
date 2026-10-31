"""Remove one mistaken distribution while preserving all other cash decisions."""
import hashlib
import json
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import now_datetime

from credinomina_reconciliation.reclassification_distribution import APPLICATION, CREDIT_KINDS, DEPOSIT, FIELD, allocations
from credinomina_reconciliation.rounding import money, money_float, sum_money


def _prepare(item_name, operation_id):
    from credinomina_reconciliation.deposit_reconciliation import lock_cash_pool
    from credinomina_reconciliation.paying_employers import reconciliation_companies
    initial = frappe.get_doc('CN Complementary Item', item_name)
    for permission in ('read', 'write', 'submit'):
        initial.check_permission(permission)
    entry = next((row for row in allocations(initial) if row['operation_id'] == operation_id), None)
    if not entry:
        frappe.throw(_('La distribución ya no existe. Recargue la partida.'))
    application = entry['kind'] == APPLICATION
    doctype = 'CN Accounting Import' if application else 'CN Remittance Allocation'
    target = frappe.get_doc(doctype, entry['parent_document'])
    target.check_permission('write')
    lock_cash_pool(set(reconciliation_companies(target.employer)) | ({initial.employer} if initial.employer else set()))
    target = frappe.get_doc(doctype, target.name, for_update=True)
    target.check_permission('write')
    item = frappe.get_doc(initial.doctype, item_name, for_update=True)
    for permission in ('write', 'submit'):
        item.check_permission(permission)
    entries = allocations(item)
    entry = next((row for row in entries if row['operation_id'] == operation_id), None)
    if not entry or entry['parent_document'] != target.name or item.docstatus != 1 or target.docstatus == 2:
        frappe.throw(_('La distribución cambió. Recargue antes de eliminar.'))
    if entry.get('client'):
        frappe.get_doc('CN Client', entry['client']).check_permission('read')
    if sum_money(row.get('importe_usd') for row in entry.get('management_history') or []) != 0:
        frappe.throw(_('Revierta primero las gestiones de devolución o aplicación de esta porción.'))
    if application:
        row = frappe.get_doc('CN Source Row', entry['target'], for_update=True)
        if row.parenttype != 'CN Accounting Import' or row.parent != target.name or (row.historical_period or row.collection_period) != entry.get('period'):
            frappe.throw(_('La aplicación cambió de destino o de período. Revise sus vínculos.'))
        period_names = {entry['period']}
    else:
        if not frappe.has_permission('CN Accounting Import', 'write'):
            frappe.throw(_('No tiene permiso para actualizar la conciliación de aplicaciones.'), frappe.PermissionError)
        if target.docstatus != 1 or target.get('consolidated_deposit'):
            frappe.throw(_('Seleccione el depósito principal confirmado.'))
        row = next((row for row in target.detail_rows if row.name == entry['target']), None)
        if not row:
            frappe.throw(_('No está disponible la fila original de la distribución.'))
        from credinomina_reconciliation.complementary_cancellation import _deposit_periods
        period_names = _deposit_periods([target])
    period_names = set(period_names) - {None, ''}
    if item.get('period'):
        period_names.add(item.period)
    periods = []
    for name in sorted(period_names):
        period = frappe.get_doc('CN Reconciliation Period', name)
        period.check_permission('write')
        if period.status == 'Cerrado':
            frappe.throw(_('Reabra el período {0} antes de eliminar la distribución.').format(name))
        periods.append(period)
    token = hashlib.sha256(json.dumps([item.modified, entries, target.as_dict(), row.as_dict()],
                                     default=str, sort_keys=True).encode()).hexdigest()
    return item, target, row, entries, entry, periods, token


@frappe.whitelist()
def preview(item_name, operation_id):
    item, target, row, entries, entry, periods, token = _prepare(item_name, operation_id)
    return dict(token=token, destination=target.name, row=row.name, kind=entry['kind'], amount_usd=entry['amount_usd'],
                remaining_usd=money_float(abs(money(item.amount_usd)) - sum_money(e['amount_usd'] for e in entries) + money(entry['amount_usd'])))


@frappe.whitelist(methods=['POST'])
def remove(item_name, operation_id, token, reason):
    if not str(reason or '').strip():
        frappe.throw(_('Indique el motivo de eliminación de la distribución.'))
    item, target, row, entries, entry, periods, current = _prepare(item_name, operation_id)
    if not token or token != current:
        frappe.throw(_('La partida o el destino cambió. Compruebe nuevamente antes de eliminar.'))
    remaining = [value for value in entries if value['operation_id'] != operation_id]
    audit = json.loads(item.get('reclassification_deletions') or '[]')
    if not isinstance(audit, list):
        frappe.throw(_('El historial de eliminaciones no es válido.'))
    audit.append(dict(operation_id=uuid4().hex, removed=entry, reason=str(reason).strip(),
                      user=frappe.session.user, recorded_on=str(now_datetime())))
    frappe.db.savepoint('remove_reclassification')
    try:
        frappe.db.set_value(item.doctype, item.name, {FIELD: frappe.as_json(remaining),
            'reclassification_deletions': frappe.as_json(audit)})
        from credinomina_reconciliation.reconciliation_trace import capture_saved_item
        capture_saved_item(item.name)
        if entry['kind'] == APPLICATION:
            from credinomina_reconciliation.credit_application import rebuild_destination
            rebuild_destination(next(period for period in periods if period.name == entry['period']))
        else:
            _remove_deposit_portion(target, row, entry, periods)
        from credinomina_reconciliation.complementary_state import mark
        mark([item.name], deposits=[target.name] if entry['kind'] != APPLICATION else [])
        item.clear_cache()
        item.add_comment('Info', _('Se eliminó una distribución. La evidencia y el motivo se conservan en la auditoría protegida.'))
        target.add_comment('Info', _('Se retiró una distribución; se recalcularon sus saldos.'))
    except Exception:
        frappe.db.rollback(save_point='remove_reclassification')
        frappe.clear_document_cache(item.doctype, item.name)
        frappe.clear_document_cache(target.doctype, target.name)
        raise
    return dict(remaining_usd=money_float(abs(money(item.amount_usd)) - sum_money(e['amount_usd'] for e in remaining)))


def _remove_deposit_portion(doc, row, entry, periods):
    from credinomina_reconciliation.deposit_presentation import coverage_totals
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
    amount = money(entry['amount_usd'])
    others = {detail.name: {key: detail.get(key) for key in ('linked_usd', 'client_credit_usd', 'company_credit_usd', 'pending_usd', 'matched_targets', 'match_status')}
              for detail in doc.detail_rows if detail.name != row.name}
    credit = entry['kind'] in CREDIT_KINDS
    logs = json.loads(doc.allocation_detail or '[]')
    removed = [e for e in logs if e.get('reclassification_operation_id') == entry['operation_id']]
    if sum_money(e.get('importe_usd') for e in removed) != (0 if credit else amount):
        frappe.throw(_('El importe distribuido no coincide con el diario del depósito. No se eliminó la porción.'))
    if money(doc.amount_usd) != money(doc.allocated_usd) + money(doc.justified_surplus_usd) + money(doc.unclassified_usd):
        frappe.throw(_('Los saldos del depósito no coinciden con su efectivo. Revise antes de eliminar.'))
    field = ('client_credit_usd' if entry['kind'] == DEPOSIT else 'company_credit_usd') if credit else 'linked_usd'
    if money(row.get(field)) < amount:
        frappe.throw(_('La fila no contiene el importe que se intenta retirar.'))
    retained = [e for e in logs if e not in removed]
    values = dict(allocation_detail=frappe.as_json(retained),
        allocated_usd=money_float(money(doc.allocated_usd) - (0 if credit else amount)),
        justified_surplus_usd=money_float(money(doc.justified_surplus_usd) - (amount if credit else 0)),
        unallocated_usd=money_float(money(doc.unallocated_usd) + (0 if credit else amount)),
        unclassified_usd=money_float(money(doc.unclassified_usd) + amount),
        detail_status='Revisar filas', result='Revisar detalle', financial_status='Revisar detalle')
    values.update(coverage_totals({**doc.as_dict(), **values}))
    frappe.db.set_value(doc.doctype, doc.name, values)
    matched = json.loads(row.matched_targets or '[]')
    matched = [e for e in matched if e.get('instruction_id') != 'RECLASS:' + entry['operation_id']]
    frappe.db.set_value(row.doctype, row.name, {field: money_float(money(row.get(field)) - amount),
        'pending_usd': money_float(money(row.pending_usd) + amount), 'matched_targets': frappe.as_json(matched),
        'matched_targets_summary': _('Distribución retirada. Consulte los vínculos restantes y seleccione los destinos manualmente.'),
        'manual_reconciliation_required': 1, 'match_status': 'Pendiente manual',
        'match_reason': _('Se eliminó una distribución. Pendiente de selección manual.')}, update_modified=False)
    # Rebuild from persisted cash; never spend the newly released amount.
    for period in periods:
        engine._reconcile_sources(period.employer, preserve_deposits=True, period_name=period.name)
    doc.clear_cache()
    doc.reload()
    if json.loads(doc.allocation_detail or '[]') != retained:
        frappe.throw(_('La operación cambiaría otros vínculos del depósito. No se guardó la eliminación.'))
    for detail in doc.detail_rows:
        if detail.name in others and any(detail.get(key) != value for key, value in others[detail.name].items()):
            frappe.throw(_('Otra fila cambió. No se guardó la eliminación; revise sus vínculos.'))
