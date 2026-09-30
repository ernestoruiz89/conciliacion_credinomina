"""Rename display vocabulary without renaming documents or recalculating money."""
import json

import frappe


LABELS = {
    "Distribución de Remesa": "Distribución de Depósito",
    "Distribuir remesa": "Distribuir depósito",
    "Distribuciones de remesas": "Distribuciones de depósitos",
    "Conciliación de remesas": "Conciliación de depósitos",
}
STATES = {"Remesa conciliada": "Depósito conciliado", "Remesa parcial": "Depósito parcial"}


def renamed_workspace_content(content):
    try:
        blocks = json.loads(content or "[]")
    except (ValueError, TypeError):
        return content
    if not isinstance(blocks, list):
        return content
    changed = False
    for block in blocks:
        data = block.get("data") if isinstance(block, dict) else None
        if not isinstance(data, dict):
            continue
        for field in ("card_name", "shortcut_name"):
            if data.get(field) in LABELS:
                data[field] = LABELS[data[field]]
                changed = True
    return json.dumps(blocks, ensure_ascii=False) if changed else content


def execute():
    for doctype, field in (("CN Source Row", "deposit_match_status"),
                           ("CN Collection Row", "application_status")):
        for old, new in STATES.items():
            frappe.db.set_value(doctype, {field: old}, field, new, update_modified=False)

    # Update only known labels on this app's workspaces, retaining custom cards,
    # routes, IDs and their ordering. No wholesale reload of the Workspace.
    for row in frappe.get_all("Workspace", filters={"module": "Conciliacion Credinomina"}, pluck="name"):
        workspace = frappe.get_doc("Workspace", row)
        content = renamed_workspace_content(workspace.content)
        if content != workspace.content:
            frappe.db.set_value("Workspace", workspace.name, "content", content, update_modified=False)
        for child in list(workspace.links or []) + list(workspace.shortcuts or []):
            if child.label in LABELS:
                frappe.db.set_value(child.doctype, child.name, "label", LABELS[child.label], update_modified=False)

    # An old site translation can override the app's updated es.csv.
    for row in frappe.get_all("Translation", filters={
        "language": "es", "source_text": "CN Remittance Allocation",
        "translated_text": "Distribución de Remesa",
    }, pluck="name"):
        frappe.db.set_value("Translation", row, "translated_text", "Distribución de Depósito", update_modified=False)
    frappe.clear_cache()
