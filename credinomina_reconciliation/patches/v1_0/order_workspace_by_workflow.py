"""Refresh the app's navigation, retaining site-specific access links/blocks."""

import json
from copy import deepcopy
from pathlib import Path


WORKSPACE = "Conciliacion Credinomina"
OLD_CARDS = {"Preparación y cobranza", "Conciliación de depósitos", "Control y consultas"}
OLD_BLOCK_IDS = {
    "cn_titulo", "cn_tablero", "cn_periodo", "cn_importar", "cn_cartera",
    "cn_distribuir", "cn_estado", "cn_excepciones", "cn_movimientos",
    "cn_espacio", "cn_procesos", "cn_preparacion", "cn_remesas", "cn_consultas",
}


def build_layout(current, standard):
    links = deepcopy(standard["links"])
    shortcuts = deepcopy(standard["shortcuts"])
    blocks = json.loads(standard["content"])
    owned_cards = OLD_CARDS | {row["label"] for row in links if row["type"] == "Card Break"}
    owned_targets = {row["link_to"] for row in links if row["type"] == "Link"}
    owned_ids = OLD_BLOCK_IDS | {block["id"] for block in blocks}

    # Keep site-added links in custom cards. Extra links inside our replaced
    # cards are moved to a dedicated card so that no destination disappears.
    extra_links, custom_links = [], []
    in_owned_card = True
    for row in current.get("links", []):
        if row["type"] == "Card Break":
            in_owned_card = row["label"] in owned_cards
            if not in_owned_card:
                custom_links.append(deepcopy(row))
        elif row.get("link_to") not in owned_targets:
            (extra_links if in_owned_card else custom_links).append(deepcopy(row))
    if extra_links:
        label = "Accesos adicionales del sitio"
        custom_labels = {row.get("label") for row in custom_links if row["type"] == "Card Break"}
        while label in custom_labels:
            label += " ·"
        links += [{"type": "Card Break", "label": label, "link_count": len(extra_links)}, *extra_links]
        blocks.append({"id": "cn_accesos_adicionales", "type": "card", "data": {"card_name": label, "col": 4}})
    links += custom_links
    extra_shortcuts = [deepcopy(row) for row in current.get("shortcuts", [])
                       if row.get("link_to") not in owned_targets]
    shortcuts += extra_shortcuts
    custom_shortcut_names = {row["label"] for row in extra_shortcuts}
    used_ids = {block["id"] for block in blocks}
    for block in json.loads(current.get("content") or "[]"):
        data = block.get("data", {})
        if block.get("type") == "card" and data.get("card_name") in owned_cards:
            continue
        if block.get("type") == "shortcut" and data.get("shortcut_name") not in custom_shortcut_names:
            continue
        if block.get("id") in owned_ids:
            continue
        block = deepcopy(block)
        while block.get("id") in used_ids:
            block["id"] += "_site"
        used_ids.add(block.get("id"))
        blocks.append(block)
    card = None
    for row in links:
        if row["type"] == "Card Break":
            card = row
            card["link_count"] = 0
        elif card is not None:
            card["link_count"] += 1
    return {"links": links, "shortcuts": shortcuts,
            "content": json.dumps(blocks, ensure_ascii=False, separators=(",", ":"))}


def execute():
    import frappe

    if not frappe.db.exists("Workspace", WORKSPACE):
        return
    path = Path(frappe.get_app_path(
        "credinomina_reconciliation", "conciliacion_credinomina", "workspace",
        "conciliacion_credinomina", "conciliacion_credinomina.json",
    ))
    standard = json.loads(path.read_text(encoding="utf-8"))
    workspace = frappe.get_doc("Workspace", WORKSPACE)
    # Newer Framework versions require the workspace kind; older records
    # imported from v15 may not have it yet.
    if workspace.meta.has_field("type") and not workspace.get("type"):
        workspace.type = "Workspace"
    layout = build_layout(workspace.as_dict(), standard)
    for field, value in layout.items():
        workspace.set(field, value)
    # Existing custom child rows retain their old idx when Document.set is
    # used; reset it so database reloads preserve the newly assembled order.
    for field in ("links", "shortcuts"):
        for index, row in enumerate(workspace.get(field), 1):
            row.idx = index
    workspace.save(ignore_permissions=True)
    frappe.clear_cache()
