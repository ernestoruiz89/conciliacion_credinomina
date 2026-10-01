"""Exercise real Frappe naming and Link updates; roll back all test fixtures."""

import frappe


def run():
    frappe.set_user("Administrator")
    marker = "PN" + frappe.generate_hash(length=8)
    try:
        employers = []
        for suffix in ("A", "B"):
            employers.append(frappe.get_doc({
                "doctype": "CN Employer", "employer_name": marker + suffix,
                "employer_code": marker + suffix, "payroll_frequency": "Mensual",
            }).insert())

        def period(day):
            return frappe.get_doc({
                "doctype": "CN Reconciliation Period", "employer": employers[0].name,
                "payroll_month": "2025-04-01", "reconciliation_mode": "Historica",
                "historical_scope": "Fecha exacta", "historical_application_date": f"2025-04-{day}",
            }).insert()

        first, second = period("15"), period("30")
        assert first.name == f"{marker}A-4-2025-01", first.name
        assert second.name == f"{marker}A-4-2025-02", second.name
        linked = frappe.get_doc({
            "doctype": "CN Accounting Import", "source_type": "Movimientos contables",
            "source_file": "/private/files/naming-check.xlsx", "status": "Importado",
            "employer": employers[0].name, "historical_period": first.name,
        }).insert()
        old = first.name
        first.payroll_month = "2025-05-01"
        first.save()
        assert first.name == f"{marker}A-5-2025-01", first.name
        assert first.localname == old
        assert not frappe.db.exists(first.doctype, old)
        assert frappe.db.get_value(linked.doctype, linked.name, "historical_period") == first.name
        first.notes = "Guardar sin consumir otro consecutivo"
        first.save()
        assert first.name == f"{marker}A-5-2025-01"

        second.employer = employers[1].name
        second.save()
        assert second.name == f"{marker}B-4-2025-01", second.name
        return {"ok": True, "checks": ["consecutivo por empresa y mes", "renombrado al guardar",
                "vínculos actualizados", "nombre estable al editar notas"], "rolled_back": True}
    finally:
        frappe.db.rollback()
