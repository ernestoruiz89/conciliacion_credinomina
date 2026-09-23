"""Expose the aging report in workspaces imported before the link was added.

Frappe does not replace a standard Workspace when its database modified time is
newer than the JSON timestamp. Add only the missing link, preserving any other
workspace changes made by the site administrator.
"""

import frappe


WORKSPACE = "Conciliacion Credinomina"
REPORT = "Antiguedad de Saldos"


def execute():
    if not frappe.db.exists("Workspace", WORKSPACE) or not frappe.db.exists("Report", REPORT):
        return
    workspace = frappe.get_doc("Workspace", WORKSPACE)
    if not any(link.link_to == REPORT for link in workspace.links):
        frappe.get_doc({
            "doctype": "Workspace Link",
            "parent": WORKSPACE,
            "parenttype": "Workspace",
            "parentfield": "links",
            "idx": len(workspace.links) + 1,
            "type": "Link",
            "label": "Antigüedad de saldos",
            "link_type": "Report",
            "link_to": REPORT,
            "is_query_report": 1,
            "report_ref_doctype": "CN Reconciliation Period",
        }).db_insert()
        workspace = frappe.get_doc("Workspace", WORKSPACE)
    for index, link in enumerate(workspace.links):
        if link.type == "Card Break" and link.label == "Control y consultas":
            next_card = next(
                (position for position in range(index + 1, len(workspace.links))
                 if workspace.links[position].type == "Card Break"),
                len(workspace.links),
            )
            count = sum(
                item.type == "Link" for item in workspace.links[index + 1:next_card]
            )
            if link.link_count != count:
                frappe.db.set_value("Workspace Link", link.name, "link_count", count)
            break
    frappe.clear_cache()
