"""Store existing import coverage for list columns without replaying reconciliation."""

import frappe

from credinomina_reconciliation.accounting_client_summary import stored_balance_totals


def execute():
    frappe.reload_doc("conciliacion_credinomina", "doctype", "cn_accounting_import")
    last = ""
    while True:
        names = frappe.get_all("CN Accounting Import", filters={"name": [">", last]},
                               pluck="name", order_by="name asc", limit_page_length=200)
        if not names:
            return
        for name in names:
            document = frappe.get_doc("CN Accounting Import", name)
            totals = stored_balance_totals(document)
            frappe.db.set_value("CN Accounting Import", name, {
                "application_amount_usd": totals["amount_usd"],
                "deposit_coverage_usd": totals["deposits_usd"],
                "complementary_coverage_usd": totals["complementary_usd"],
                "balance_usd": totals["balance_usd"],
            }, update_modified=False)
        last = names[-1]
