"""Prepare two-stage workflow without reconciling or changing stored balances."""
import json

import frappe

from credinomina_reconciliation.legacy_employer_detail import archive_values


def execute():
    for name in frappe.get_all("CN Reconciliation Period", pluck="name", limit_page_length=0):
        period = frappe.get_doc("CN Reconciliation Period", name)
        if not period.get("legacy_employer_detail"):
            evidence = archive_values(period)
            if evidence:
                frappe.db.set_value("CN Reconciliation Period", name, "legacy_employer_detail",
                                    json.dumps(evidence, ensure_ascii=False, default=str), update_modified=False)
        # Closed records and real, materialized adjustments remain untouched.
        if period.status == "Cerrado":
            continue
        for proposal in period.get("provisional_adjustments") or []:
            if proposal.state != "Materializado" and proposal.basis != "Cobranza":
                frappe.db.set_value(proposal.doctype, proposal.name,
                                    {"state": "Pendiente de revisión", "approved_by": None,
                                     "approved_on": None, "fingerprint": ""}, update_modified=False)
