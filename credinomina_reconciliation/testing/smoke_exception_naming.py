"""Verify readable numbering and existing document links without persisting data."""
import re
import frappe
from frappe.utils import now_datetime


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    doctype = "CN Reconciliation Exception"
    try:
        marker = frappe.generate_hash(length=10)
        employer = frappe.get_doc({
            "doctype": "CN Employer", "employer_name": f"Smoke Naming {marker}",
            "employer_code": f"SN{marker}", "payroll_frequency": "Mensual",
        }).insert()
        period = frappe.get_doc({
            "doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2025-04-01", "reconciliation_mode": "Historica",
            "collection_cycle": "Mensual",
        }).insert()
        old_name = frappe.generate_hash(length=10)
        old = frappe.get_doc({"doctype": doctype, "name": old_name,
                              "exception_type": "Prueba previa", "status": "Abierta"})
        old.db_insert()
        old = frappe.get_doc(doctype, old_name)
        old.description = "Actualización que conserva el enlace anterior"
        old.save()
        assert old.name == old_name and frappe.db.exists(doctype, old_name)
        first = frappe.get_doc({"doctype": doctype, "exception_type": "Caso manual", "status": "Abierta",
                                "period": period.name, "employer": employer.name}).insert()
        second = frappe.get_doc({"doctype": doctype, "exception_type": "Caso automático", "status": "Abierta",
                                 "period": period.name, "employer": employer.name,
                                 "exception_key": "DED-TEST-" + frappe.generate_hash(length=10)}).insert()
        assert re.fullmatch(r"CN-EXC-4-2025-\d{3,}", first.name)
        assert re.fullmatch(r"CN-EXC-4-2025-\d{3,}", second.name)
        assert int(second.name.rsplit("-", 1)[1]) == int(first.name.rsplit("-", 1)[1]) + 1
        standalone = frappe.get_doc({"doctype": doctype, "exception_type": "Sin período", "status": "Abierta"}).insert()
        assert re.fullmatch(rf"CN-EXC-{now_datetime().year}-\d{{4,}}", standalone.name)
        initial_name = standalone.name
        standalone.period = period.name
        standalone.employer = employer.name
        standalone.save()
        standalone.reload()
        assert standalone.name == initial_name and standalone.period == period.name
        another = frappe.get_doc({
            "doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2025-05-01", "reconciliation_mode": "Historica",
            "collection_cycle": "Mensual",
        }).insert()
        first_name = first.name
        first.period = another.name
        first.save()
        first.reload()
        assert first.name == first_name and first.period == another.name
        standalone.period = None
        standalone.save()
        assert standalone.name == initial_name
        assert frappe.db.get_value(doctype, {"exception_key": second.exception_key}, "name") == second.name
        return {"first": first.name, "second": second.name, "without_period": standalone.name, "existing_link_preserved": True,
                "automatic_key_preserved": True, "period_changes_preserve_name": True, "rolled_back": True}
    finally:
        frappe.db.rollback()
