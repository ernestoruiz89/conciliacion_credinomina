"""Preserve the former single period without changing financial/audit records."""
import frappe


def execute():
    if not frappe.db.has_column("CN Remittance Allocation", "detail_period"):
        return
    deposits = frappe.db.sql("""SELECT name, detail_period, docstatus
        FROM `tabCN Remittance Allocation`
        WHERE COALESCE(detail_period, '') != ''""", as_dict=True)
    for deposit in deposits:
        if not frappe.db.exists("CN Remittance Period", {
            "parent": deposit.name, "parenttype": "CN Remittance Allocation",
            "parentfield": "detail_periods", "period": deposit.detail_period,
        }):
            period = frappe.db.get_value("CN Reconciliation Period", deposit.detail_period,
                                         ["employer", "applied_usd"], as_dict=True)
            if not period:
                frappe.throw(f"El depósito {deposit.name} tiene un período inexistente: {deposit.detail_period}.")
            count = frappe.db.count("CN Remittance Period", {"parent": deposit.name,
                "parenttype": "CN Remittance Allocation", "parentfield": "detail_periods"})
            frappe.get_doc({"doctype": "CN Remittance Period", "parent": deposit.name,
                "parenttype": "CN Remittance Allocation", "parentfield": "detail_periods",
                "period": deposit.detail_period, "employer": period.employer,
                "applied_usd": period.applied_usd, "docstatus": deposit.docstatus,
                "idx": count + 1}).db_insert()
        # Do not touch modified, confirmation, allocations or closed periods.
        # Clearing the old column makes a repeated patch harmless after edits.
        frappe.db.sql("UPDATE `tabCN Remittance Allocation` SET detail_period=NULL WHERE name=%s", deposit.name)
