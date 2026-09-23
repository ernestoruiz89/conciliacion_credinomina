"""Exercise period close, write lock and explicit reopening on a disposable site."""

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import (
    close_period,
    reopen_period,
)
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_source_import.cn_source_import import (
    reconcile_all_sources,
)


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Esta prueba solo puede ejecutarse en el sitio desechable.")
    frappe.set_user("Administrator")
    try:
        period = frappe.get_doc({
            "doctype": "CN Reconciliation Period",
            "employer": "Simulación Convenio Alfa",
            "payroll_month": "2027-01-01",
            "reconciliation_mode": "Operativa",
            "collection_cycle": "Mensual",
        }).insert()
        period.status = "Cerrado"
        try:
            period.save()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Se cerró el período sin usar la acción de cierre.")
        period.reload()
        close_period(period.name)
        closed = frappe.get_doc("CN Reconciliation Period", period.name)
        assert closed.status == "Cerrado"
        assert closed.status_before_close == "Borrador"
        assert closed.closed_on and closed.closed_by == "Administrator"
        reconcile_all_sources()
        assert frappe.db.get_value("CN Reconciliation Period", period.name, "status") == "Cerrado"
        closed.reload()
        closed.notes = "Cambio no autorizado"
        closed.flags.allow_closed_reconciliation = True  # Un indicador del documento no autoriza la escritura.
        try:
            closed.save()
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("El período cerrado permitió guardar cambios.")
        result = reopen_period(period.name, "Corrección verificada")
        assert result["status"] == "Borrador"
        reopened = frappe.get_doc("CN Reconciliation Period", period.name)
        assert reopened.reopen_reason == "Corrección verificada"
        assert reopened.reopened_on and reopened.reopened_by == "Administrator"
        reopened.notes = "Cambio posterior a reapertura"
        reopened.save()
        close_period(period.name)
        try:
            reopen_period(period.name, "")
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Se reabrió un período sin motivo.")
        historical = frappe.get_doc({
            "doctype": "CN Reconciliation Period",
            "employer": "Simulación Convenio Alfa",
            "payroll_month": "2025-04-01",
            "reconciliation_mode": "Historica",
            "historical_scope": "Mensual",
            "status": "Historico conciliado",
        }).insert()
        close_period(historical.name)
        assert reopen_period(historical.name, "Revisión histórica")["status"] == "Historico conciliado"
        return {
            "period": period.name, "closed_write_blocked": True,
            "reopened": True, "historical_reopened": True,
        }
    finally:
        frappe.db.rollback()
