"""Repair stale import headers from persisted reconciliation results only."""

import frappe


SUMMARY_FIELDS = ("row_count", "matched_count", "exception_count", "ignored_count", "status")


def execute():
    for name in frappe.get_all("CN Accounting Import", filters={
        "status": ["in", ["Importado", "Importado con excepciones"]],
        "docstatus": ["!=", 2],
    }, pluck="name", limit_page_length=0):
        document = frappe.get_doc("CN Accounting Import", name)
        previous = {field: document.get(field) for field in SUMMARY_FIELDS}
        document.recalculate_reconciliation_summary()
        changes = {field: document.get(field) for field in SUMMARY_FIELDS
                   if document.get(field) != previous[field]}
        if changes:
            # Do not save/reimport/reconcile: that could alter financial evidence,
            # allocations, or closed periods. Only the derived header is repaired.
            frappe.db.set_value(document.doctype, name, changes, update_modified=False)
            frappe.clear_document_cache(document.doctype, name)
