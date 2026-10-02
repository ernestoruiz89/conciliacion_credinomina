"""Exercise draft creation without touching production or committing fixtures."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_period import create_draft_period, get_period_defaults


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    marker = "DRAFT-" + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, "enqueue"):
            employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker,
                "employer_code": marker, "payroll_frequency": "Quincenal"}).insert()
            frappe.get_doc({"doctype": "CN Accounting Import", "name": marker,
                "employer": employer.name, "status": "Importado"}).db_insert()
            frappe.get_doc({"doctype": "CN Source Row", "name": marker + "-ROW", "parent": marker,
                "parenttype": "CN Accounting Import", "parentfield": "rows", "idx": 1,
                "event_type": "Aplicacion", "event_date": "2025-04-15", "amount_usd": 100}).db_insert()
            before = frappe.get_doc("CN Accounting Import", marker).as_dict()
            defaults = get_period_defaults(marker)
            historical = create_draft_period(marker, defaults)
            assert historical["status"] == "Borrador"
            doc = frappe.get_doc("CN Reconciliation Period", historical["name"])
            assert doc.docstatus == 0 and not doc.collection_rows and not doc.applied_usd
            assert doc.historical_scope == "Fecha exacta"
            try:
                create_draft_period(marker, defaults)
            except frappe.ValidationError:
                pass
            else:
                raise AssertionError("Duplicate period accepted")
            operative = create_draft_period(marker, {"payroll_month": "2026-09-15", "collection_cycle": "Primera quincena"})
            doc = frappe.get_doc("CN Reconciliation Period", operative["name"])
            assert doc.status == "Borrador" and doc.reconciliation_mode == "Operativa"
            assert doc.collection_cycle == "Primera quincena" and str(doc.payroll_month) == "2026-09-01"
            assert frappe.get_doc("CN Accounting Import", marker).as_dict() == before
            return {"historical_and_operative_drafts": True, "duplicates_blocked": True,
                    "source_unchanged": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
