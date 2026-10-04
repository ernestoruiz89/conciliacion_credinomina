"""Temporary visual acceptance fixture. Never allowed outside the isolated test site."""
from unittest.mock import patch
import frappe


def _site():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para cn-reconciliation-test.local")
    frappe.set_user("Administrator")


def create():
    _site()
    # Keep a $20 credit with a $5 partial refund available for browser QA.
    # This isolated fixture deliberately commits so the browser can read it.
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import create_complementary_item
    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import record_control_cut
    from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
    from credinomina_reconciliation.client_credit import record_management
    from frappe.utils.file_manager import save_file
    marker = "UI-HARDENING-" + frappe.generate_hash(length=8)
    with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
        company = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker}).insert()
        period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": company.name, "payroll_month": "2025-04-01",
            "reconciliation_mode": "Historica", "historical_scope": "Fecha exacta", "historical_application_date": "2025-04-30"}).insert()
        source = frappe.get_doc({"doctype": "CN Accounting Import", "employer": company.name,
            "currency": "USD", "source_file": "/private/files/synthetic-ui.csv", "status": "Importado",
            "rows": [{"event_type": "Aplicacion", "event_date": "2025-04-30", "source_key": marker,
                "client_name": "Cliente de prueba visual", "client_number": marker, "loan_number": "999980001-1",
                "currency": "USD", "amount": 80, "amount_usd": 80, "effective": 1,
                "historical_period": period.name, "processing_route": "Historica"}]}).insert()
        _reconcile_sources(company.name)
        deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "employer": company.name,
            "deposit_reference": marker, "deposit_date": "2025-05-10", "deposit_currency": "USD", "deposit_amount": 100,
            "detail_periods": [{"period": period.name}], "targets": [{"historical_application": source.rows[0].name, "amount_usd": 80}]}).insert()
        deposit.submit()
        reconcile_deposit(deposit)
        deposit.reload()
        created = create_complementary_item(deposit.name, str(deposit.modified), {
            "category": "Saldo a favor de la empresa", "amount": 20, "currency": "USD", "posting_date": "2025-05-10",
            "reason_type": "Error de la empresa", "description": "Excedente de prueba visual: devolución parcial pendiente",
            "credit_assigned_to": "Administrator", "credit_commitment_date": "2026-10-10", "credit_treatment": "Devolución"})
        item = frappe.get_doc("CN Complementary Item", created["name"])
        support = save_file("soporte_sintetico.txt", b"SOPORTE SINTETICO - NO ES UN PAGO REAL", item.doctype, item.name, is_private=1)
        record_management(item.name, str(item.modified), "Devolución", 5, "2025-05-20", "PRUEBA-NO-REAL", support.file_url)
        record_control_cut(period.name, "Prueba visual de corte guardado, no representa datos reales")
        frappe.db.commit()
    return {"employer": marker, "period": period.name, "deposit": deposit.name, "item": item.name}


def cleanup(employer):
    _site()
    if not employer.startswith("UI-HARDENING-"):
        raise RuntimeError("No es una empresa sintética de esta prueba")
    with patch.object(frappe, "enqueue"):
        for doctype in ("CN Complementary Item", "CN Remittance Allocation", "CN Accounting Import", "CN Reconciliation Period", "CN Employer"):
            names = frappe.get_all(doctype, filters={"name" if doctype == "CN Employer" else "employer": employer}, pluck="name")
            if not names:
                continue
            for name in frappe.get_all("File", filters={"attached_to_doctype": doctype, "attached_to_name": ["in", names]}, pluck="name"):
                frappe.delete_doc("File", name, ignore_permissions=True)
            for field in frappe.get_meta(doctype).get_table_fields():
                frappe.db.delete(field.options, {"parenttype": doctype, "parent": ["in", names]})
            frappe.db.delete("Version", {"ref_doctype": doctype, "docname": ["in", names]})
            frappe.db.delete("Comment", {"reference_doctype": doctype, "reference_name": ["in", names]})
            frappe.db.delete(doctype, {"name": ["in", names]})
        frappe.db.commit()
    return {"removed_synthetic_employer": employer}
