"""Backfill documentation only, including closed periods and confirmed deposits."""
import frappe

from credinomina_reconciliation.difference_reasons import seed_reasons
from credinomina_reconciliation.immaterial_differences import classify_document
from credinomina_reconciliation.report_records import _pages


def execute():
    seed_reasons()
    scopes = {
        "CN Reconciliation Period": {"docstatus": ["!=", 2], "reconciliation_mode": ["!=", "Historica"], "no_collection": 0},
        "CN Accounting Import": {"docstatus": ["!=", 2], "status": ["in", ["Importado", "Importado con excepciones"]]},
        "CN Remittance Allocation": {"docstatus": 1},
    }
    for doctype, filters in scopes.items():
        for row in _pages(frappe.get_all, doctype, filters=filters, fields=["name"], order_by="name asc"):
            document = frappe.get_doc(doctype, row.name, for_update=True)
            classify_document(document)
