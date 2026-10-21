"""Expose the client workbench without changing financial records."""
import frappe


def execute():
    from credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow import execute as refresh_workspace

    frappe.reload_doc('conciliacion_credinomina', 'page', 'mesa_conciliacion')
    refresh_workspace()
    if not frappe.db.exists('DocType', 'Workspace Sidebar'):
        return
    if not frappe.db.exists('Workspace Sidebar', 'Conciliacion Credinomina'):
        return
    sidebar = frappe.get_doc('Workspace Sidebar', 'Conciliacion Credinomina')
    if any(row.link_type == 'Page' and row.link_to == 'mesa-conciliacion' for row in sidebar.get('items')):
        return
    item = sidebar.append('items', dict(type='Link', label='Mesa de conciliación',
        link_type='Page', link_to='mesa-conciliacion', icon='arrow-left-right', child=0,
        collapsible=1, keep_closed=0, indent=0, show_arrow=0))
    rows = sidebar.get('items')
    rows.remove(item)
    index = next((index + 1 for index, row in enumerate(rows)
                  if row.link_type == 'Page' and row.link_to == 'control-credinomina'), 0)
    rows.insert(index, item)
    for index, row in enumerate(rows, 1):
        row.idx = index
    sidebar.save(ignore_permissions=True)
    frappe.clear_cache()
