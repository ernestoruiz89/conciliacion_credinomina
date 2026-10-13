"""Index presentation states without changing money, result or audit journals."""
import frappe
from credinomina_reconciliation.deposit_presentation import reconciliation_status
from credinomina_reconciliation.report_records import _pages


def execute():
    for row in _pages(frappe.get_all, "CN Remittance Allocation", filters={},
                      fields=["name", "docstatus", "result", "financial_status", "amount_usd",
                              "allocated_usd", "justified_surplus_usd", "unclassified_usd", "detail_status"],
                      order_by="name asc"):
        state = reconciliation_status(row)
        if row.financial_status != state:
            frappe.db.set_value("CN Remittance Allocation", row.name, "financial_status", state,
                                update_modified=False)
