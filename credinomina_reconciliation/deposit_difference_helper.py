"""Edit deposit detail classifications without saving/reconciling financial rows."""
import frappe
from frappe import _

from credinomina_reconciliation.difference_documentation import _store_documentation, _touch_documentation
from credinomina_reconciliation.rounding import money, money_float

DOCTYPE = 'CN Remittance Allocation'


def difference_rows(document):
    result = []
    for row in document.get('detail_rows') or []:
        company = row.get('row_kind') == 'Diferencia documentada'
        credit = money(row.get('client_credit_usd')) + money(row.get('company_credit_usd'))
        if not (company or money(row.get('pending_usd')) or credit
                or row.get('difference_reason') or row.get('difference_notes')):
            continue
        result.append(dict(row_name=row.name, source_row=row.get('source_row') or row.get('idx'),
            client_name=row.get('client_name'), loan_number=row.get('loan_number'), employer=row.get('employer'),
            row_kind=row.get('row_kind') or 'Cliente', amount_usd=money_float(row.get('amount_usd')),
            linked_usd=money_float(money(row.get('linked_usd')) + credit),
            pending_usd=money_float(row.get('difference_core_pending_usd') if company else row.get('pending_usd')),
            match_status=row.get('match_status'), match_reason=row.get('match_reason'),
            difference_reason=row.get('difference_reason') or '', difference_notes=row.get('difference_notes') or ''))
    return result


@frappe.whitelist()
def load(name):
    document = frappe.get_doc(DOCTYPE, name)
    document.check_permission('read')
    document.check_permission('write')
    if document.docstatus == 2:
        frappe.throw(_('No se puede documentar un depósito cancelado.'))
    return dict(modified=str(document.modified), rows=difference_rows(document))


@frappe.whitelist(methods=['POST'])
def save(name, modified, rows):
    frappe.db.get_value(DOCTYPE, name, 'name', for_update=True)
    document = frappe.get_doc(DOCTYPE, name)
    document.check_permission('write')
    if document.docstatus == 2:
        frappe.throw(_('No se puede documentar un depósito cancelado.'))
    if str(document.modified) != str(modified):
        frappe.throw(_('El depósito cambió. Cierre y vuelva a abrir el helper antes de guardar.'))
    if isinstance(rows, str):
        rows = frappe.parse_json(rows)
    if not isinstance(rows, list) or not rows:
        frappe.throw(_('No hay cambios de clasificación para guardar.'))
    eligible = {row['row_name'] for row in difference_rows(document)}
    children = {row.name: row for row in document.detail_rows or []}
    checked, seen, reasons = [], set(), set()
    for change in rows:
        if not isinstance(change, dict) or set(change) - {'row_name', 'difference_reason', 'difference_notes'}:
            frappe.throw(_('Solo puede editar la razón y los comentarios de las filas del detalle.'))
        row_name = change.get('row_name')
        if not isinstance(row_name, str) or row_name not in eligible or row_name in seen:
            frappe.throw(_('La fila no pertenece a las diferencias de este depósito o está repetida.'))
        seen.add(row_name)
        reason = str(change.get('difference_reason') or '').strip()
        if not reason:
            frappe.throw(_('Seleccione una razón de diferencia para cada fila modificada.'))
        if reason not in reasons:
            if not frappe.db.exists('CN Difference Reason', reason):
                frappe.throw(_('Seleccione una razón de la diferencia válida.'))
            frappe.get_doc('CN Difference Reason', reason).check_permission('read')
            reasons.add(reason)
        checked.append((children[row_name], dict(difference_reason=reason,
            difference_notes=str(change.get('difference_notes') or '').strip())))
    # Validate every row and reason before the first write. Frappe rolls back
    # the entire request if any persistence or audit operation fails.
    updated = 0
    for row, after in checked:
        if _store_documentation(document, row, after):
            updated += 1
        row.update(after)
    version = _touch_documentation(document) if updated else str(document.modified)
    return dict(modified=version, updated=updated, rows=difference_rows(document))
