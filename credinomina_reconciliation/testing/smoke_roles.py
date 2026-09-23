"""Exercise operator and supervisor access on the isolated WSL test site."""

import frappe

from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import (
    get_control_data,
)
from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos.antiguedad_de_saldos import (
    execute as aging_report,
)


TEST_SITE = "cn-reconciliation-test.local"
USERS = (
    ("operador@credinomina-test.invalid", "Operador Credinomina"),
    ("supervisor@credinomina-test.invalid", "Supervisor Credinomina"),
)


def run():
    if frappe.local.site != TEST_SITE:
        raise RuntimeError(f"Solo puede ejecutarse en {TEST_SITE}")
    frappe.set_user("Administrator")
    for email, role in USERS:
        if not frappe.db.exists("User", email):
            user = frappe.get_doc({
                "doctype": "User", "email": email,
                "first_name": "Prueba", "last_name": role,
                "user_type": "System User", "enabled": 1,
                "send_welcome_email": 0,
                "roles": [{"role": role}],
            })
            user.flags.no_welcome_mail = True
            user.insert(ignore_permissions=True)
    results = {}
    for email, role in USERS:
        frappe.set_user(email)
        if not frappe.has_permission("CN Reconciliation Period", "read"):
            raise AssertionError(f"{role} no puede leer períodos.")
        if not frappe.has_permission("CN Source Import", "write"):
            raise AssertionError(f"{role} no puede importar aplicaciones.")
        if not frappe.has_permission("CN Remittance Allocation", "write"):
            raise AssertionError(f"{role} no puede registrar depósitos.")
        can_submit = frappe.has_permission("CN Remittance Allocation", "submit")
        if can_submit != (role == "Supervisor Credinomina"):
            raise AssertionError(f"Permiso de confirmación incorrecto: {role}")
        dashboard = get_control_data(year=2026)
        _columns, rows, _message, _chart, summary = aging_report({
            "as_of_date": "2027-02-01", "to_month": "2026-11-01",
        })
        operative_periods = sum(
            row["reconciliation_mode"] == "Operativa"
            for row in dashboard["periods"]
        )
        if operative_periods != 10 or len(rows) != 37 or not summary:
            raise AssertionError(f"{role} no puede consultar el control y la antigüedad.")
        results[role] = {
            "dashboard_periods": len(dashboard["periods"]),
            "aging_rows": len(rows), "can_submit_deposits": bool(can_submit),
        }
    frappe.set_user("Administrator")
    frappe.db.commit()
    return results
