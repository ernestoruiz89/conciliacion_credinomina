"""Make the monthly transaction matrix accessible on existing sites."""
import frappe


def execute():
    frappe.reload_doc("conciliacion_credinomina", "report", "transacciones_por_empresa")
    from credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow import execute as refresh_workspace
    refresh_workspace()
