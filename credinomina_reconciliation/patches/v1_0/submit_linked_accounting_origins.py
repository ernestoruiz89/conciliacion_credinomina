"""Confirm legacy linked drafts through the same validated, nonfinancial path."""
import frappe

from credinomina_reconciliation import manual_origin_link as origins


def execute():
    frappe.reload_doc('conciliacion_credinomina', 'doctype', 'cn_complementary_item')
    deposits = sorted(set(frappe.get_all(origins.ITEM,
        filters={'docstatus': 0, 'origin_deposit': ['is', 'set']}, pluck='origin_deposit')))
    for deposit in deposits:
        names = frappe.get_all(origins.ITEM, filters={'origin_deposit': deposit}, pluck='name')
        frappe.db.savepoint('submit_linked_accounting_origins')
        try:
            result = origins.preview(deposit, names)
            if not result['already_linked'] or result['issues']:
                frappe.log_error(title='Origen contable pendiente de confirmar',
                    message=f'{deposit}: vínculo inconsistente o diferencias: {result["issues"]}')
                continue
            origins.link(deposit, names, result['token'], 'Migración: confirmar el origen contable ya vinculado.')
        except frappe.ValidationError:
            frappe.db.rollback(save_point='submit_linked_accounting_origins')
            frappe.log_error(title='Origen contable pendiente de confirmar',
                message=f'{deposit}\n{frappe.get_traceback()}')
