"""Describe existing detail targets without rerunning financial reconciliation."""

import frappe

from credinomina_reconciliation.remittance_target_summary import (
    describe_targets, load_target_descriptions, read_targets,
)


def execute():
    if not frappe.db.table_exists("CN Remittance Detail"):
        return
    rows = frappe.get_all("CN Remittance Detail",
        fields=["name", "matched_targets"], limit_page_length=0)
    descriptions = load_target_descriptions([
        target for row in rows for target in (read_targets(row.matched_targets) or [])
    ])
    for row in rows:
        frappe.db.set_value("CN Remittance Detail", row.name, "matched_targets_summary",
                            describe_targets(row.matched_targets or "[]", descriptions), update_modified=False)
