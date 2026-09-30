import json
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.patches.v1_0 import rename_remittance_display_to_deposit as migration


class DepositTerminologyMigrationTests(unittest.TestCase):
    def test_workspace_keeps_custom_content_ids_and_order(self):
        blocks = [
            {"id": "cn_remesas", "type": "card", "data": {"card_name": "Conciliación de remesas", "col": 4}},
            {"id": "custom", "type": "header", "data": {"text": "Mi nota sobre remesas"}},
            {"id": "shortcut", "data": {"shortcut_name": "Distribuir remesa"}},
        ]
        result = migration.renamed_workspace_content(json.dumps(blocks))
        updated = json.loads(result)
        self.assertEqual([row["id"] for row in updated], [row["id"] for row in blocks])
        self.assertEqual(updated[1], blocks[1])
        self.assertEqual(updated[0]["data"], {"card_name": "Conciliación de depósitos", "col": 4})
        self.assertEqual(updated[2]["data"]["shortcut_name"], "Distribuir depósito")
        self.assertEqual(migration.renamed_workspace_content(result), result)
        self.assertEqual(migration.renamed_workspace_content("invalid"), "invalid")

    def test_existing_states_are_updated_without_saving_documents_or_reconciling(self):
        workspace = frappe._dict(name="W", content="[]", links=[
            frappe._dict(doctype="Workspace Link", name="L", label="Distribuciones de remesas"),
        ], shortcuts=[])
        with patch.object(migration.frappe, "db", Mock()) as db, \
             patch.object(migration.frappe, "get_all", side_effect=[["W"], ["T"]]), \
             patch.object(migration.frappe, "get_doc", return_value=workspace), \
             patch.object(migration.frappe, "clear_cache") as clear:
            migration.execute()
        db.set_value.assert_any_call("CN Source Row", {"deposit_match_status": "Remesa conciliada"},
                                     "deposit_match_status", "Depósito conciliado", update_modified=False)
        db.set_value.assert_any_call("CN Collection Row", {"application_status": "Remesa parcial"},
                                     "application_status", "Depósito parcial", update_modified=False)
        db.set_value.assert_any_call("Translation", "T", "translated_text", "Distribución de Depósito", update_modified=False)
        clear.assert_called_once()
