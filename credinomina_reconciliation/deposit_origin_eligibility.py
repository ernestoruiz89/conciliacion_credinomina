"""A core receipt may become evidence only while its cash is wholly unused."""
import json

import frappe
from frappe import _

from credinomina_reconciliation.rounding import money

USAGE_FIELDS = ('allocated_usd', 'justified_surplus_usd', 'credit_coverage_usd',
                'complementary_coverage_usd', 'conversion_coverage_usd')
DETAIL_FIELDS = ('detail_file', 'detail_source_file', 'detail_hash', 'detail_imported_on',
                 'detail_origin', 'detail_count', 'detail_total_usd', 'applied_usd')


def unused_cash_issues(origin):
    issues = []
    if origin.get('docstatus') not in (0, 1):
        issues.append(_('El origen del core debe estar en borrador o confirmado, nunca cancelado.'))
    if any(origin.get(field) for field in (*DETAIL_FIELDS, 'detail_rows', 'detail_periods', 'targets')):
        issues.append(_('El depósito del core tiene detalle, períodos o destinos. Revise sus vínculos antes de usarlo como origen.'))
    try:
        entries = json.loads(origin.get('allocation_detail') or '[]')
        if not isinstance(entries, list):
            raise ValueError()
    except (TypeError, ValueError):
        issues.append(_('La distribución del depósito del core no es legible; revise su evidencia.'))
    else:
        if entries:
            issues.append(_('El depósito del core ya tiene asignaciones guardadas; no puede vincularse como otro origen.'))
    if any(money(origin.get(field)) for field in USAGE_FIELDS):
        issues.append(_('El depósito del core ya tiene efectivo distribuido o justificado.'))
    return issues


def assert_no_cash_dependencies(origin, canonical_name):
    """Use unrestricted evidence checks under the company's shared cash lock."""
    for child in ('CN Remittance Target', 'CN Remittance Detail', 'CN Remittance Period'):
        if frappe.db.exists(child, {'parent': origin.name, 'parenttype': 'CN Remittance Allocation'}):
            frappe.throw(_('El depósito del core tiene detalle o destinos guardados. No se modificó ningún vínculo.'))
    if frappe.get_all('CN Complementary Item', filters={'docstatus': ['<', 2]}, or_filters={
            'registered_deposit': origin.name, 'origin_deposit': origin.name, 'deposit_source_row': origin.name},
            pluck='name', limit_page_length=1):
        frappe.throw(_('El depósito del core respalda partidas o saldos a favor. Revise esos vínculos antes de vincularlo.'))
    if frappe.get_all('CN Reconciliation Period', or_filters={
            'prepared_deposit': origin.name, 'deduction_recognition_deposit': origin.name},
            pluck='name', limit_page_length=1):
        frappe.throw(_('El depósito del core está vinculado a un período. Se conservó su conciliación.'))
    # Reclassification journals can reserve cash without a registered_deposit header.
    token = '%' + json.dumps(origin.name).replace('%', '\\%').replace('_', '\\_') + '%'
    if frappe.db.exists('CN Complementary Item', {'docstatus': ['<', 2], 'reclassification_allocations': ['like', token]}):
        frappe.throw(_('El depósito del core tiene una distribución contable vinculada. Revise su historial.'))
    if frappe.get_all('CN Remittance Allocation', filters={'name': ['not in', [origin.name, canonical_name]],
            'docstatus': ['<', 2]}, or_filters={'consolidated_deposit': origin.name, 'linked_core_deposit': origin.name},
            pluck='name', limit_page_length=1):
        frappe.throw(_('Otro depósito depende de este origen del core. Se conservaron sus vínculos.'))
