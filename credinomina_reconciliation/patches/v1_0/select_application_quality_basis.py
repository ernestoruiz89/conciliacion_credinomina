"""Initialize one comparison base per period without rewriting cash or uploads."""
import frappe

from credinomina_reconciliation.application_quality import COLLECTION, EMPLOYER_DETAIL, VALID_DEDUCTION


def choose_basis(period):
    materialized = {row.basis for row in period.get("provisional_adjustments") or []
                    if row.state == "Materializado"}
    if materialized == {COLLECTION}:
        return COLLECTION
    if materialized and materialized <= {EMPLOYER_DETAIL, "Detalle de deducción"}:
        return EMPLOYER_DETAIL
    if len(materialized) > 1:
        # Old mixed evidence cannot be silently reinterpreted as one base.
        return None
    if period.get("employer_response_file") or any(
        row.deduction_status in VALID_DEDUCTION for row in period.collection_rows or []
    ):
        return EMPLOYER_DETAIL
    return COLLECTION


def execute():
    for name in frappe.get_all("CN Reconciliation Period", filters={"reconciliation_mode": "Operativa"}, pluck="name"):
        period = frappe.get_doc("CN Reconciliation Period", name)
        if period.get("application_basis"):
            continue
        basis = choose_basis(period)
        if basis:
            frappe.db.set_value(period.doctype, name, "application_basis", basis, update_modified=False)
        else:
            period.add_comment("Comment", "Revisar base de primera conciliación: este período trasladó bases mixtas antes de la selección única. Se conservan sus datos y saldos; no reprocesar sin revisar el depósito vinculado.")
        for row in period.get("provisional_adjustments") or []:
            if row.state != "Materializado":
                frappe.db.set_value(row.doctype, row.name, {
                    "state": "Pendiente de revisión", "approved_by": None, "approved_on": None,
                }, update_modified=False)
