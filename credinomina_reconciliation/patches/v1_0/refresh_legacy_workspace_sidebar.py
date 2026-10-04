"""Repair generated v16 sidebar links without replacing site customizations."""
import frappe


SIDEBAR = "Conciliacion Credinomina"


def changes_for(row):
    if row.get("type") != "Link" or row.get("link_type") != "DocType":
        return {}
    target = row.get("link_to")
    label = row.get("label")
    if target == "CN Remittance Allocation" and label == "Distribuir remesa":
        return {"label": "Distribuir depósito"}
    if target == "CN Accounting Import" and label == "Importar fuentes":
        return {"label": "Movimientos contables"}
    if target == "CN Reconciliation Movement" and label == "Movimientos de conciliación":
        # A filtered or custom navigation entry may carry semantics that cannot
        # safely be translated to the unified complementary DocType.
        if any(row.get(field) not in (None, "", "[]", "{}")
               for field in ("filters", "route_options", "navigate_to_tab")):
            return {}
        return {"link_to": "CN Complementary Item", "label": "Partidas complementarias"}
    return {}


def execute():
    if not frappe.db.exists("DocType", "Workspace Sidebar"):
        return  # Frappe v15.
    if not frappe.db.exists("Workspace Sidebar", SIDEBAR):
        return
    sidebar = frappe.get_doc("Workspace Sidebar", SIDEBAR)
    changed = False
    for row in sidebar.get("items") or []:
        values = changes_for(row)
        if values:
            # Only labels and an obsolete standard target change. Leave row
            # identity, ordering, icons, filters and access configuration alone.
            frappe.db.set_value(row.doctype, row.name, values, update_modified=False)
            changed = True
    if changed:
        frappe.clear_cache()
