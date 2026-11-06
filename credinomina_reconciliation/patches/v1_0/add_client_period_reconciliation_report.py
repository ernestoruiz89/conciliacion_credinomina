"""Expose a read-only report; preserve all financial records and custom links."""
import frappe


REPORT = 'Conciliacion por Cliente y Periodo'


def execute():
    frappe.reload_doc('conciliacion_credinomina', 'report', 'conciliacion_por_cliente_y_periodo')
    from credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow import execute as refresh_workspace
    refresh_workspace()
    if not frappe.db.exists('DocType', 'Workspace Sidebar') or not frappe.db.exists('Workspace Sidebar', 'Conciliacion Credinomina'):
        return
    sidebar = frappe.get_doc('Workspace Sidebar', 'Conciliacion Credinomina')
    if any(row.link_type == 'Report' and row.link_to == REPORT for row in sidebar.get('items')):
        return
    item = sidebar.append('items', dict(type='Link', label='Conciliación por cliente y período',
        link_type='Report', link_to=REPORT, icon='clipboard-check', child=1,
        collapsible=1, keep_closed=0, indent=0, show_arrow=0))
    rows = sidebar.get('items')
    rows.remove(item)
    index = next((index + 1 for index, row in enumerate(rows)
                  if row.link_type == 'Report' and row.link_to == 'Resumen de Conciliacion'), len(rows))
    rows.insert(index, item)
    for index, row in enumerate(rows, 1):
        row.idx = index
    sidebar.save(ignore_permissions=True)
    frappe.clear_cache()
