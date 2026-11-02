"""Keep full manual-destination validation reasons instead of a 140-char field."""
import frappe


def execute():
    frappe.reload_doc("conciliacion_credinomina", "doctype", "cn_remittance_target")
