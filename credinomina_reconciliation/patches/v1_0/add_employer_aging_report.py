"""Add the summary report next to aging without replacing customized workspaces."""
import frappe


def execute():
    frappe.reload_doc("conciliacion_credinomina", "report", "antiguedad_de_saldos_por_empresa")
    name = "Conciliacion Credinomina"
    if not frappe.db.exists("Workspace", name):
        return
    workspace = frappe.get_doc("Workspace", name)
    report = "Antiguedad de Saldos por Empresa"
    if any(link.link_to == report for link in workspace.links):
        return
    anchor = next((link for link in workspace.links if link.link_to == "Antiguedad de Saldos"), None)
    if not anchor:
        return
    for link in reversed(workspace.links):
        if link.idx > anchor.idx:
            frappe.db.set_value("Workspace Link", link.name, "idx", link.idx + 1, update_modified=False)
    frappe.get_doc({"doctype": "Workspace Link", "parent": name, "parenttype": "Workspace",
        "parentfield": "links", "idx": anchor.idx + 1, "type": "Link",
        "label": "Antigüedad de saldos por empresa", "link_type": "Report", "link_to": report,
        "is_query_report": 1, "report_ref_doctype": "CN Reconciliation Period"}).db_insert()
    links = frappe.get_doc("Workspace", name).links
    card = None
    for link in links:
        if link.type == "Card Break":
            card = link
        if link.link_to == report:
            break
    if card:
        end = next((link.idx for link in links if link.type == "Card Break" and link.idx > card.idx), len(links) + 1)
        count = sum(link.type == "Link" for link in links if card.idx < link.idx < end)
        frappe.db.set_value("Workspace Link", card.name, "link_count", count, update_modified=False)
    frappe.clear_cache()
