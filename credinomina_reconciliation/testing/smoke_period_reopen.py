"""Rollback-only rejection of empty period closure on the disposable site.

The real close/reopen path uses a liquidated period in
``smoke_closed_operative_reconciliation.run``.
"""

from uuid import uuid4

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import (
    close_period,
)


def _must_reject_empty(period):
    try:
        close_period(period.name)
    except frappe.ValidationError:
        pass
    else:
        raise AssertionError(f"Se cerró el período vacío {period.name}.")
    assert frappe.db.get_value("CN Reconciliation Period", period.name, "status") != "Cerrado"


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Esta prueba solo puede ejecutarse en el sitio desechable.")
    frappe.set_user("Administrator")
    marker = uuid4().hex[:10]
    try:
        employer = frappe.get_doc({
            "doctype": "CN Employer", "employer_name": f"Smoke Empty {marker}",
            "employer_code": f"SE{marker}", "payroll_frequency": "Mensual",
        }).insert(ignore_permissions=True)
        operative = frappe.get_doc({
            "doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2027-01-01", "reconciliation_mode": "Operativa",
            "collection_cycle": "Mensual",
        }).insert(ignore_permissions=True)
        _must_reject_empty(operative)

        historical = frappe.get_doc({
            "doctype": "CN Reconciliation Period", "employer": employer.name,
            "payroll_month": "2025-04-01", "reconciliation_mode": "Historica",
            "historical_scope": "Mensual", "status": "Historico conciliado",
        }).insert(ignore_permissions=True)
        _must_reject_empty(historical)
        print({
            "operative_empty_rejected": True,
            "historical_empty_rejected": True,
            "rolled_back": True,
        })
    finally:
        frappe.db.rollback()
