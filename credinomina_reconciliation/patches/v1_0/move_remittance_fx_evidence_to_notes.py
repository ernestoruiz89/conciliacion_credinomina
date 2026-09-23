"""Retain the old remittance FX source before removing its DocType field."""

import frappe

from credinomina_reconciliation.parsers import clean_text


def execute():
    if not frappe.db.has_column("CN Remittance Allocation", "fx_evidence"):
        return
    rows = frappe.db.sql(
        """select name, notes, fx_evidence
           from `tabCN Remittance Allocation`
           where coalesce(fx_evidence, '') != ''""",
        as_dict=True,
    )
    for row in rows:
        evidence = clean_text(row.fx_evidence)
        notes = clean_text(row.notes)
        if not evidence or evidence in notes:
            continue
        label = f"Fuente de la tasa de cambio: {evidence}"
        frappe.db.set_value(
            "CN Remittance Allocation", row.name, "notes",
            f"{notes}\n{label}" if notes else label,
            update_modified=False,
        )
