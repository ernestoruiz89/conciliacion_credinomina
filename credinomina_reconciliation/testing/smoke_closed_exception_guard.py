"""Rollback-only smoke: closed periods reject exception and action writes."""

from uuid import uuid4

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import (
    reopen_period,
)


def _must_reject(action):
    try:
        action()
    except frappe.ValidationError:
        return
    raise AssertionError("Se modificó una excepción de un período cerrado.")


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Esta prueba solo puede ejecutarse en el sitio desechable.")
    frappe.set_user("Administrator")
    marker = uuid4().hex[:10]
    try:
        employer = frappe.get_doc({
            "doctype": "CN Employer", "employer_name": f"Smoke Exception {marker}",
            "employer_code": f"SX{marker}", "payroll_frequency": "Mensual",
        }).insert(ignore_permissions=True)
        period = frappe.get_doc({
            "doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2026-10-01", "reconciliation_mode": "Operativa",
            "collection_cycle": "Mensual",
        }).insert(ignore_permissions=True)
        another = frappe.get_doc({
            "doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2026-11-01", "reconciliation_mode": "Operativa",
            "collection_cycle": "Mensual",
        }).insert(ignore_permissions=True)
        exception = frappe.get_doc({
            "doctype": "CN Reconciliation Exception", "period": period.name,
            "employer": employer.name, "exception_type": "Caso manual",
            "status": "Abierta", "description": "Seguimiento de prueba",
        }).insert(ignore_permissions=True)
        original_count = frappe.db.get_value(
            "CN Reconciliation Period", period.name, "exception_count"
        )
        assert original_count == 1

        # Construct the closed fixture directly: this smoke tests exception
        # protection, not financial prerequisites for closing a period.
        frappe.db.set_value("CN Reconciliation Period", period.name, "status", "Cerrado")

        _must_reject(lambda: frappe.get_doc({
            "doctype": "CN Reconciliation Exception", "period": period.name,
            "employer": employer.name, "exception_type": "Caso nuevo",
            "status": "Abierta",
        }).insert(ignore_permissions=True))

        changed = frappe.get_doc("CN Reconciliation Exception", exception.name)
        changed.description = "Cambio no permitido"
        _must_reject(lambda: changed.save(ignore_permissions=True))

        action = frappe.get_doc("CN Reconciliation Exception", exception.name)
        action.append("follow_up_actions", {
            "action_type": "Contacto con empresa", "details": "Gestión no permitida",
        })
        _must_reject(lambda: action.save(ignore_permissions=True))

        moved = frappe.get_doc("CN Reconciliation Exception", exception.name)
        moved.period = another.name
        _must_reject(lambda: moved.save(ignore_permissions=True))

        _must_reject(lambda: frappe.delete_doc(
            "CN Reconciliation Exception", exception.name,
            ignore_permissions=True, force=True,
        ))
        assert frappe.db.get_value(
            "CN Reconciliation Period", period.name, "exception_count"
        ) == original_count
        assert frappe.db.exists("CN Reconciliation Exception", exception.name)

        reopen_period(period.name, "Continuar gestión de la excepción")
        allowed = frappe.get_doc("CN Reconciliation Exception", exception.name)
        allowed.append("follow_up_actions", {
            "action_type": "Contacto con empresa", "details": "Gestión tras reapertura",
        })
        allowed.save(ignore_permissions=True)
        assert len(allowed.follow_up_actions) == 1
        print({
            "insert_blocked": True, "edit_blocked": True,
            "action_blocked": True, "move_blocked": True,
            "delete_blocked": True, "count_unchanged": True,
            "reopen_allows_follow_up": True, "rolled_back": True,
        })
    finally:
        frappe.db.rollback()
