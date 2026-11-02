"""Audit amounts already represent USD; never label them with the site currency."""
import frappe


def execute():
    frappe.reload_doc('conciliacion_credinomina', 'doctype', 'cn_reconciliation_trace')
    frappe.db.set_value('CN Reconciliation Trace', {'usd_currency': ['is', 'not set']},
        'usd_currency', 'USD', update_modified=False)
