"""Batch source-row documentation without running financial save hooks."""
import frappe
from frappe import _

from credinomina_reconciliation.difference_documentation import _store_documentation, _touch_documentation
from credinomina_reconciliation.application_deposit_status import SETTLED
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.accounting_client_summary import _collections_for_import, row_amounts

DOCTYPE = 'CN Accounting Import'


def has_difference(row):
    if not row.get('effective') or row.get('match_status') == 'Ignorado' or row.get('deposit_match_status') in SETTLED:
        return False
    if row.get('event_type') == 'Aplicacion':
        return True
    if row.get('event_type') == 'Deposito' and money(row.get('unallocated_usd')):
        return True
    return row.get('match_status') in {'Ambiguo', 'Sin coincidencia'}


def detail_rows(document):
    balances = row_amounts(document, _collections_for_import(document, check_permissions=True))
    return [dict(row_name=row.name, source_row=row.get('source_row') or row.get('idx'),
        client_name=row.get('client_name'), loan_number=row.get('loan_number'),
        event_date=row.get('event_date'), event_type=row.get('event_type'), amount_usd=row.get('amount_usd'),
        balance_usd=balances.get(row.name, {}).get('balance_usd'),
        deposit_match_status=row.get('deposit_match_status'), deposit_match_reason=row.get('deposit_match_reason'),
        difference_reason=row.get('difference_reason') or '', difference_notes=row.get('difference_notes') or '')
        for row in document.get('rows') or [] if has_difference(row)]


def check_document(document):
    document.check_permission('read')
    document.check_permission('write')
    if document.docstatus == 2:
        frappe.throw(_('No se puede documentar una importación cancelada.'))


@frappe.whitelist()
def load(name):
    document = frappe.get_doc(DOCTYPE, name)
    check_document(document)
    return dict(modified=str(document.modified), rows=detail_rows(document))


@frappe.whitelist(methods=['POST'])
def save(name, modified, rows):
    frappe.db.get_value(DOCTYPE, name, 'name', for_update=True)
    document = frappe.get_doc(DOCTYPE, name)
    check_document(document)
    if str(document.modified) != str(modified):
        frappe.throw(_('La importación cambió. Cierre y vuelva a abrir el modal antes de guardar.'))
    if isinstance(rows, str):
        rows = frappe.parse_json(rows)
    if not isinstance(rows, list) or not rows:
        frappe.throw(_('No hay cambios de clasificación para guardar.'))
    children = {row.name: row for row in document.get('rows') or []}
    eligible = {row.name for row in children.values() if has_difference(row)}
    checked, seen, reasons = [], set(), set()
    for change in rows:
        if not isinstance(change, dict) or set(change) - {'row_name', 'difference_reason', 'difference_notes'}:
            frappe.throw(_('Solo puede editar la razón y la documentación de la diferencia.'))
        row_name = change.get('row_name')
        if not isinstance(row_name, str) or row_name not in children or row_name in seen:
            frappe.throw(_('La fila no pertenece a esta importación o está repetida.'))
        if row_name not in eligible:
            frappe.throw(_('La fila ya no tiene diferencias pendientes. Cierre y vuelva a abrir el modal.'))
        row = children[row_name]
        if row.doctype != 'CN Source Row':
            frappe.throw(_('La fila no corresponde al detalle contable.'))
        seen.add(row_name)
        reason, notes = (str(change.get(field) or '').strip()
                         for field in ('difference_reason', 'difference_notes'))
        if not reason:
            frappe.throw(_('Seleccione una razón de diferencia para cada fila modificada.'))
        if not notes:
            frappe.throw(_('Documente el motivo y el soporte de la diferencia en cada fila modificada.'))
        if reason not in reasons:
            if not frappe.db.exists('CN Difference Reason', reason):
                frappe.throw(_('Seleccione una razón de la diferencia válida.'))
            frappe.get_doc('CN Difference Reason', reason).check_permission('read')
            reasons.add(reason)
        checked.append((row, dict(difference_reason=reason, difference_notes=notes)))
    # Validate the entire batch before the first write. Both documentation and
    # audit comments share the request transaction; never save/reconcile a parent.
    updated = 0
    for row, after in checked:
        updated += int(_store_documentation(document, row, after))
        row.update(after)
    version = _touch_documentation(document) if updated else str(document.modified)
    return dict(modified=version, updated=updated, rows=detail_rows(document))
