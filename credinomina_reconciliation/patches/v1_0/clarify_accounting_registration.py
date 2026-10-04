"""Reclassify legacy voucher-only claims without changing cash or source evidence."""
import frappe
from credinomina_reconciliation.complementary_exceptions import apply_registration_status


def execute():
    start = 0
    while True:
        rows = frappe.get_all("CN Complementary Item", fields=["*"], order_by="name asc",
                              limit_start=start, limit_page_length=300)
        if not rows:
            break
        for item in rows:
            previous = item.accounting_status
            apply_registration_status(item)
            if item.accounting_status != previous:
                frappe.db.set_value("CN Complementary Item", item.name, "accounting_status",
                                    item.accounting_status, update_modified=False)
                frappe.clear_document_cache("CN Complementary Item", item.name)
        start += len(rows)
