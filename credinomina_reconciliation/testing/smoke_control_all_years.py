"""Verify all-year dashboard and export queries on the disposable Frappe site."""
import frappe
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import get_control_data, export_control_excel


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    marker = "all-years-" + frappe.generate_hash(length=8)
    try:
        employer = frappe.get_doc({"doctype": "CN Employer", "employer_name": marker, "employer_code": marker}).insert()
        names = []
        for year, amount in ((2025, 100), (2026, 200)):
            period = frappe.get_doc({"doctype": "CN Reconciliation Period", "employer": employer.name,
                                     "payroll_month": f"{year}-04-01", "reconciliation_mode": "Historica"}).insert()
            frappe.db.set_value(period.doctype, period.name, "applied_usd", amount)
            names.append(period.name)
        # A deposit in a different year must not disappear in the all-year view.
        deposit = frappe.get_doc({"doctype": "CN Remittance Allocation", "name": marker, "docstatus": 1,
                                  "employer": employer.name, "deposit_reference": marker, "deposit_date": "2027-01-10",
                                  "deposit_currency": "USD", "deposit_amount": 50, "amount_usd": 50,
                                  "unallocated_usd": 50, "unclassified_usd": 50, "allocation_detail": "[]"})
        deposit.db_insert()
        one = get_control_data(2025, employer.name)
        assert len(one["periods"]) == 1 and one["totals"]["applied_usd"] == 100
        data = get_control_data("Todos", employer.name)
        assert data["year"] == "Todos" and set(p["name"] for p in data["periods"]) == set(names)
        assert data["totals"]["applied_usd"] == 300
        assert data["available_years"] == [2027, 2026, 2025]
        assert any(d["parent"] == marker for d in data["open_deposits"])
        export_control_excel("Todos", employer.name)
        assert "Todos" in frappe.local.response.filename
        assert frappe.local.response.filecontent[:2] == b"PK"
        return {"all_years": True, "totals": 300, "other_year_deposit_visible": True,
                "year_catalog": data["available_years"], "export": "ok", "rolled_back": True}
    finally:
        frappe.db.rollback()
