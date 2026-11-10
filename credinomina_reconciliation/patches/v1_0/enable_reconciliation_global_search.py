"""Index existing reconciliation documents without resaving financial records."""
import frappe
from frappe.utils.global_search import rebuild_for_doctype

from credinomina_reconciliation.hooks import global_search_doctypes


def execute():
    doctypes = [entry['doctype'] for entry in global_search_doctypes['Default']]
    settings = frappe.get_single('Global Search Settings')
    existing = {row.document_type for row in settings.get('allowed_in_global_search') or []}
    missing = [doctype for doctype in doctypes if doctype not in existing]
    for doctype in missing:
        settings.append('allowed_in_global_search', {'document_type': doctype})
    if missing:
        settings.save(ignore_permissions=True)

    frappe.cache.delete_value('doctypes_with_global_search')
    for doctype in doctypes:
        frappe.clear_cache(doctype=doctype)
        rebuild_for_doctype(doctype)
