"""Preserve stored full descriptions; never reimport or change original amounts."""
import frappe


def execute():
    frappe.db.sql("""update `tabCN Source Row` set source_description=description
        where (source_description is null or source_description='') and description is not null""")
    frappe.db.sql("""update `tabCN Source Row` set source_fx_rate=case
        when manual_fx_rate>0 then manual_fx_rate else fx_rate end
        where source_currency='NIO' and (source_fx_rate is null or source_fx_rate=0)
        and (manual_fx_rate>0 or fx_rate>0)""")
    frappe.reload_doc("conciliacion_credinomina", "report", "control_mensual_de_movimientos_contables")
    # Existing helper preserves site-specific links while applying the app's navigation.
    from credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow import execute as refresh_workspace
    refresh_workspace()
