"""Expose clients on existing Workspaces without replacing administrator edits."""

import frappe


WORKSPACE = "Conciliacion Credinomina"
CARD = "Preparación y cobranza"


def execute():
    if not frappe.db.exists("Workspace", WORKSPACE):
        return
    workspace = frappe.get_doc("Workspace", WORKSPACE)
    links = workspace.links
    card_index = next(
        (index for index, item in enumerate(links)
         if item.type == "Card Break" and item.label == CARD), None,
    )
    if card_index is None:
        return
    next_card = next(
        (index for index in range(card_index + 1, len(links))
         if links[index].type == "Card Break"), len(links),
    )
    if not any(item.link_to == "CN Client" for item in links):
        insert_idx = links[next_card].idx if next_card < len(links) else len(links) + 1
        for item in reversed(links):
            if item.idx >= insert_idx:
                frappe.db.set_value(
                    "Workspace Link", item.name, "idx", item.idx + 1,
                    update_modified=False,
                )
        frappe.get_doc({
            "doctype": "Workspace Link",
            "parent": WORKSPACE,
            "parenttype": "Workspace",
            "parentfield": "links",
            "idx": insert_idx,
            "type": "Link",
            "label": "Clientes y alias",
            "link_type": "DocType",
            "link_to": "CN Client",
        }).db_insert()
        workspace = frappe.get_doc("Workspace", WORKSPACE)
        links = workspace.links
        card_index = next(index for index, item in enumerate(links)
                          if item.type == "Card Break" and item.label == CARD)
        next_card = next(
            (index for index in range(card_index + 1, len(links))
             if links[index].type == "Card Break"), len(links),
        )
    count = sum(item.type == "Link" for item in links[card_index + 1:next_card])
    frappe.db.set_value(
        "Workspace Link", links[card_index].name, "link_count", count,
        update_modified=False,
    )
    frappe.clear_cache()
