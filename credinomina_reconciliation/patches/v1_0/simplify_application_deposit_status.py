"""Rename only source-row statuses, preserving money, links and audit dates."""
import frappe

from credinomina_reconciliation.application_deposit_status import RENAMED


def execute():
    for old, new in RENAMED.items():
        frappe.db.set_value("CN Source Row", {
            "parenttype": "CN Accounting Import", "parentfield": "rows",
            "deposit_match_status": old,
        }, "deposit_match_status", new, update_modified=False)
