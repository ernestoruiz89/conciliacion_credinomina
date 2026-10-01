"""Move the existing table and references before Frappe creates the new schema."""

import frappe
from frappe import _


OLD = "CN Source Import"
NEW = "CN Accounting Import"


def execute():
    if not frappe.db.exists("DocType", OLD):
        return
    if frappe.db.exists("DocType", NEW) or frappe.db.table_exists(NEW):
        frappe.throw(_(
            "Existen ambas importaciones ({0} y {1}). Revise los registros antes de migrar; no se fusionarán automáticamente."
        ).format(OLD, NEW))
    # Frappe renames the SQL table, DocFields, child parenttype, permissions,
    # Link/Dynamic Link fields, attachments and customizations together.
    frappe.rename_doc("DocType", OLD, NEW, force=True, merge=False,
                      show_alert=False, rebuild_search=False)
    frappe.clear_cache()
