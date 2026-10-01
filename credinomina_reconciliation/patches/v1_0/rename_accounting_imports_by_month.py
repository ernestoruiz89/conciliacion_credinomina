"""Rename existing imports without recalculating allocations or changing row IDs."""

import frappe

from credinomina_reconciliation.accounting_naming import (
    DOCTYPE, accounting_month, accounting_prefix, matches_accounting_prefix, new_accounting_name,
)


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    for record in frappe.get_all(DOCTYPE, fields=["name", "employer"],
                                 order_by="creation asc, name asc", limit_page_length=0):
        rows = frappe.get_all("CN Source Row", filters={
            "parent": record.name, "parenttype": DOCTYPE, "parentfield": "rows",
        }, fields=["event_date"], limit_page_length=0)
        month = accounting_month(rows)
        prefix = accounting_prefix(record.employer, month)
        if matches_accounting_prefix(record.name, prefix):
            continue
        if not prefix and record.name.startswith("CONTA-BORRADOR-"):
            continue
        frappe.rename_doc(DOCTYPE, record.name, new_accounting_name(prefix),
                          force=True, merge=False, show_alert=False, rebuild_search=False)
    frappe.clear_cache()
