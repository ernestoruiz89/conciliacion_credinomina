"""Correct a deposit item's credit without rewriting its distributions."""
import frappe
from frappe import _

from credinomina_reconciliation.parsers import canonical_credit_number, clean_text

EDITABLE_CATEGORIES = {'Cobranza administrativa', 'Otros ingresos', 'Ajuste de conciliación'}


def validate_submitted_loan(doc, previous):
    if not previous or previous.get('docstatus') != 1 or doc.get('docstatus') != 1:
        return
    if clean_text(doc.get('loan_number')) == clean_text(previous.get('loan_number')):
        return
    if (doc.get('category') not in EDITABLE_CATEGORIES or doc.get('review_action') != 'Partida de depósito'
            or doc.get('generic_distribution') or doc.get('compensations')
            or any(doc.get(field) for field in ('origin_deposit', 'receivable_origin', 'registration_exception'))
            or doc.get('subcategory_effect') == 'CxC al Empleado'):
        frappe.throw(_('Este tratamiento conserva el crédito confirmado. Revise sus vínculos antes de corregirlo.'))

    from credinomina_reconciliation.complementary_cancellation import _deposit_periods
    from credinomina_reconciliation.deposit_reconciliation import entries
    names = set(frappe.get_all('CN Remittance Target', filters={
        'complementary_item': doc.name, 'parenttype': 'CN Remittance Allocation', 'docstatus': ['<', 2],
    }, pluck='parent', limit_page_length=0))
    # Automatic allocations can exist without a manual target child.
    for deposit in frappe.get_all('CN Remittance Allocation', filters={
        'docstatus': 1, 'allocation_detail': ['like', '%' + doc.name + '%'],
    }, fields=['name', 'allocation_detail'], limit_page_length=0):
        if any(entry.get('partida') == doc.name for entry in entries(deposit.allocation_detail)):
            names.add(deposit.name)
    deposits = []
    credit = canonical_credit_number(doc.get('loan_number'))
    old_credit = canonical_credit_number(previous.get('loan_number'))
    for name in sorted(names):
        deposit = frappe.get_doc('CN Remittance Allocation', name)
        if deposit.docstatus == 2:
            continue
        deposit.check_permission('read')
        deposits.append(deposit)
        details = {row.name: row for row in deposit.detail_rows or []}
        linked = [target for target in deposit.targets if target.complementary_item == doc.name]
        for target in linked:
            detail = details.get(target.detail_row)
            if detail and clean_text(detail.loan_number) and credit != canonical_credit_number(detail.loan_number):
                frappe.throw(_('El depósito {0}, fila {1}, requiere el crédito {2}. El cambio dejaría incompatible ese vínculo.').format(
                    deposit.name, detail.source_row or detail.idx, detail.loan_number))
        automatic = any(entry.get('partida') == doc.name for entry in entries(deposit.allocation_detail)) and not linked
        if automatic and old_credit and credit != old_credit:
            frappe.throw(_('Desconcilie el depósito {0} antes de cambiar el crédito de su partida asignada automáticamente.').format(deposit.name))
    periods = {doc.get('period'), previous.get('period')} - {None, ''}
    periods.update(_deposit_periods(deposits))
    closed = frappe.get_all('CN Reconciliation Period', filters={
        'name': ['in', sorted(periods)], 'status': 'Cerrado',
    }, pluck='name', limit_page_length=0) if periods else []
    if closed:
        frappe.throw(_('Reabra los períodos {0} antes de corregir el crédito de esta partida.').format(', '.join(sorted(closed))))
