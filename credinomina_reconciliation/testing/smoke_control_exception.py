"""Dashboard exception creation using real Frappe, with rolled-back fixtures."""
import frappe
from credinomina_reconciliation.control_exceptions import create_application_exception, annotate_application_exceptions
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import get_control_data


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    marker = "control-exc-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker}).insert()
        period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                                 "payroll_month": "2025-04-01", "reconciliation_mode": "Historica"}).insert()
        source = frappe.get_doc({"doctype": "CN Accounting Import", "name": marker, "employer": employer.name,
                                 "status": "Borrador"})
        source.db_insert()
        row = frappe.get_doc({"doctype": "CN Source Row", "name": marker, "parent": source.name,
                              "parenttype": source.doctype, "parentfield": "rows", "idx": 1, "source_row": 2,
                              "event_type": "Aplicacion", "effective": 1, "match_status": "Conciliado",
                              "currency": "USD", "amount": 100, "historical_remitted_usd": 70,
                              "historical_balance_usd": 30, "historical_period": period.name,
                              "client_name": "Ana Prueba", "client_number": "12", "loan_number": "100-1",
                              "event_date": "2025-04-15"})
        row.db_insert()
        result = create_application_exception(period.name, row.name, 30, "Depósito parcial", "Otro")
        doc = frappe.get_doc("CN Reconciliation Exception", result["name"])
        assert result["created"] and doc.amount_usd == 30 and doc.related_case_id == row.name
        assert doc.client_number == "12" and doc.source_row == 2 and doc.period == period.name
        control = get_control_data(year=2025, employer=employer.name)
        card = next(p for p in control["periods"] if p["name"] == period.name)
        assert card["historical_rows"][0].exception_name == doc.name
        assert card["exceptions"][0].name == doc.name
        again = create_application_exception(period.name, row.name, 30, "Reintento", "Otro")
        assert not again["created"] and again["name"] == doc.name
        assert frappe.db.count(doc.doctype, {"related_case_id": row.name}) == 1
        data = row.as_dict()
        annotate_application_exceptions([data])
        assert data.exception_name == doc.name and data.exception_status == "Abierta"
        row.reload()
        assert row.historical_remitted_usd == 70 and row.historical_balance_usd == 30
        frappe.db.set_value(period.doctype, period.name, "status", "Cerrado")
        try:
            create_application_exception(period.name, row.name, 30, "No permitido", "Otro")
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Se permitió registrar sobre un período cerrado.")
        return {"created": True, "linked_to_application": True, "duplicate_prevented": True,
                "view_exception_available": True, "balance_unchanged": True, "closed_period_protected": True}
    finally:
        frappe.db.rollback()
