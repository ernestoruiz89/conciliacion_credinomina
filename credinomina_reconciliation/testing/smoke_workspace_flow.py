"""Validate the navigation migration on the disposable Frappe site."""

import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow import (
    WORKSPACE, execute,
)


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo puede ejecutarse en cn-reconciliation-test.local")
    frappe.set_user("Administrator")
    try:
        before = frappe.get_doc("Workspace", WORKSPACE)
        roles = [row.role for row in before.roles]
        # Emulate the patch runner and prevent developer-mode file exports.
        with patch.dict(frappe.flags, {"in_patch": True}):
            execute()
            workspace = frappe.get_doc("Workspace", WORKSPACE)
            assert [row.role for row in workspace.roles] == roles
            cards = [row.label for row in workspace.links if row.type == "Card Break"]
            assert cards[:6] == [
                "1. Preparación", "2. Cobranza y deducciones", "3. Aplicaciones del core",
                "4. Depósitos y distribución", "5. Diferencias y seguimiento", "6. Control y reportes",
            ], cards
            shortcuts = [row.label for row in workspace.shortcuts]
            assert shortcuts[:4] == ["Cargar corte de cartera", "Nuevo período", "Cargar aplicaciones", "Registrar depósito"]
            content = json.loads(workspace.content)
            for row in workspace.links:
                if row.type == "Link":
                    assert frappe.db.exists(row.link_type, row.link_to), row.as_dict()
            execute()
            again = frappe.get_doc("Workspace", WORKSPACE)
            assert json.loads(again.content) == content, {"first": content, "second": json.loads(again.content)}
            assert len(again.links) == len(workspace.links)
        return {"workflow_cards": 6, "primary_shortcuts": 4, "destinations": "OK",
                "roles_preserved": "OK", "rerun": "OK"}
    finally:
        frappe.db.rollback()
        frappe.clear_cache()
