"""Initialize display totals only; existing evidence links are NOT confirmations."""
import frappe

from credinomina_reconciliation.application_adjustments import refresh_rows
from credinomina_reconciliation.rounding import money_float, sum_money


def execute():
    for name in frappe.get_all("CN Accounting Import", pluck="name", limit_page_length=0):
        document = frappe.get_doc("CN Accounting Import", name)
        refresh_rows(document.rows or [])
        for row in document.rows or []:
            frappe.db.set_value("CN Source Row", row.name, {
                field: row.get(field) for field in ("application_adjustment_usd", "net_applied_usd", "application_adjustment_status")
            }, update_modified=False)
        applications = [row for row in document.rows or [] if row.event_type == "Aplicacion" and row.effective]
        frappe.db.set_value(document.doctype, name, {
            "total_application_adjustment_usd": money_float(sum_money(row.application_adjustment_usd for row in applications)),
            "total_net_applied_usd": money_float(sum_money(row.net_applied_usd for row in applications)),
        }, update_modified=False)
