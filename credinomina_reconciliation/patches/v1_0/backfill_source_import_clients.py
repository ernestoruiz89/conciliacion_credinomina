"""Resolve and create CN Client links for previously imported applications."""

import frappe

from credinomina_reconciliation.client_registry import (
    ClientIndex,
    enrich_source_import_clients,
)


def execute():
    parent_doctype = "CN Source Import"
    child_doctype = "CN Source Row"
    if not frappe.db.table_exists(parent_doctype) or not frappe.db.table_exists(child_doctype):
        return
    if not frappe.db.has_column(child_doctype, "client"):
        return

    imports = frappe.get_all(
        parent_doctype,
        filters={"status": ["in", ["Importado", "Importado con excepciones"]]},
        fields=["name"],
        order_by="creation asc",
        limit_page_length=1000000,
    )
    client_index = ClientIndex()
    for import_row in imports:
        document = frappe.get_doc(parent_doctype, import_row.name)
        rows = [row.as_dict() for row in (document.rows or [])]
        enrich_source_import_clients(rows, client_index=client_index)
        for row in rows:
            if row.get("event_type") != "Aplicacion":
                continue
            frappe.db.set_value(
                child_doctype,
                row["name"],
                {
                    "client": row.get("client") or "",
                    "client_registry_status": row.get("client_registry_status") or "",
                },
                update_modified=False,
            )
