"""Populate deposit list coverage from saved allocation journals."""

import frappe

from credinomina_reconciliation.deposit_presentation import coverage_totals


def execute():
    frappe.reload_doc("conciliacion_credinomina", "doctype", "cn_remittance_allocation")
    last = ""
    while True:
        deposits = frappe.get_all("CN Remittance Allocation", filters={"name": [">", last]},
                                  fields=["name", "allocated_usd", "justified_surplus_usd", "allocation_detail"],
                                  order_by="name asc", limit_page_length=200)
        if not deposits:
            return
        for deposit in deposits:
            frappe.db.set_value("CN Remittance Allocation", deposit.name,
                                coverage_totals(deposit), update_modified=False)
        last = deposits[-1].name
