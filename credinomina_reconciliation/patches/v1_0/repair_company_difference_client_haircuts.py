"""Repair saved one-cent haircuts without replaying any reconciliation."""
import frappe

from credinomina_reconciliation.company_difference_repair import repair, ReviewRequired
from credinomina_reconciliation.report_records import _pages


def execute():
    candidates = sorted({row.parent for row in _pages(frappe.get_all, "CN Remittance Detail", filters={
        "parenttype": "CN Remittance Allocation", "parentfield": "detail_rows", "docstatus": 1,
        "row_kind": "Diferencia documentada", "difference_amount_usd": -.01,
        "difference_core_item": ["is", "not set"], "difference_reason": ["is", "set"],
    }, fields=["parent"])})
    result = dict(repaired=[], review={})
    for name in candidates:
        frappe.db.savepoint("repair_company_cent")
        try:
            if repair(name):
                result["repaired"].append(name)
        except ReviewRequired as error:
            frappe.db.rollback(save_point="repair_company_cent")
            result["review"][name] = str(error)
            frappe.logger(__name__).warning("Depósito %s conservado para revisión: %s", name, error)
    return result
