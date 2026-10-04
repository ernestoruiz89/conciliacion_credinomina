import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation.patches.v1_0 import refresh_legacy_workspace_sidebar as migration


class LegacySidebarTests(unittest.TestCase):
    def row(self, **kwargs):
        return frappe._dict(type="Link", link_type="DocType", **kwargs)

    def test_updates_only_known_standard_entries(self):
        for target, label, expected in (
            ("CN Remittance Allocation", "Distribuir remesa", {"label": "Distribuir depósito"}),
            ("CN Accounting Import", "Importar fuentes", {"label": "Movimientos contables"}),
            ("CN Reconciliation Movement", "Movimientos de conciliación",
             {"link_to": "CN Complementary Item", "label": "Partidas complementarias"}),
        ):
            row = self.row(link_to=target, label=label)
            self.assertEqual(migration.changes_for(row), expected)
            row.update(expected)
            self.assertEqual(migration.changes_for(row), {})

    def test_preserves_custom_labels_filters_and_non_doctype_links(self):
        for row in (
            self.row(link_to="CN Remittance Allocation", label="Caja del equipo"),
            self.row(link_to="CN Reconciliation Movement", label="Movimientos de conciliación", filters='{"status":"Pendiente"}'),
            self.row(link_to="CN Reconciliation Movement", label="Movimientos de conciliación", navigate_to_tab="Detalle"),
            frappe._dict(type="Link", link_type="Page", link_to="CN Reconciliation Movement", label="Movimientos de conciliación"),
        ):
            self.assertEqual(migration.changes_for(row), {})

    def test_v15_without_sidebar_is_noop(self):
        with patch.object(migration.frappe, "db", Mock(exists=Mock(return_value=False))), \
             patch.object(migration.frappe, "get_doc") as get_doc:
            migration.execute()
        get_doc.assert_not_called()

    def test_migration_is_scoped_and_idempotent(self):
        row = self.row(name="ITEM", doctype="Workspace Sidebar Item",
                       link_to="CN Remittance Allocation", label="Distribuir remesa")
        database = Mock(exists=Mock(return_value=True))
        database.set_value.side_effect = lambda dt, name, values, **kw: row.update(values)
        sidebar = {"items": [row]}
        with patch.object(migration.frappe, "db", database), \
             patch.object(migration.frappe, "get_doc", return_value=sidebar) as get_doc, \
             patch.object(migration.frappe, "clear_cache") as clear:
            migration.execute()
            migration.execute()
        database.set_value.assert_called_once_with("Workspace Sidebar Item", "ITEM", {"label": "Distribuir depósito"}, update_modified=False)
        clear.assert_called_once()
        self.assertEqual(get_doc.call_args.args, ("Workspace Sidebar", migration.SIDEBAR))
