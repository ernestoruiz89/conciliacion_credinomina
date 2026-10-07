import unittest
from unittest.mock import patch

from credinomina_reconciliation import client_registry as registry
from tests.test_source_import_client_creation import FakeDocument


class ClientRegistryEfficiencyTests(unittest.TestCase):
    def client(self, **extra):
        return dict(name="CLIENT", employer="EMP", client_number="7", client_name="Ana Pérez",
                    national_id="ID7", employee_number="", client_aliases=[], **extra)

    def test_repeated_verified_portfolio_links_check_company_once(self):
        with patch.object(registry, "load_client_index", return_value=[self.client()]), \
             patch.object(registry.frappe.db, "exists", return_value=True) as exists, \
             patch.object(registry.frappe, "get_doc") as get_doc:
            index = registry.ClientIndex()
            for _ in range(200):
                name, _ = index.ensure_from_source_import({"portfolio_client": "CLIENT",
                    "client_number": "7", "client_name": "Ana Pérez", "national_id": "ID7"}, "EMP")
                self.assertEqual(name, "CLIENT")
            exists.assert_called_once_with("CN Employer", "EMP")
            get_doc.assert_not_called()

    def test_unchanged_existing_client_does_not_reload_document(self):
        with patch.object(registry, "load_client_index", return_value=[self.client()]), \
             patch.object(registry.frappe.db, "exists", return_value=True), \
             patch.object(registry.frappe, "get_doc") as get_doc:
            index = registry.ClientIndex()
            for _ in range(20):
                name, _ = index.ensure_from_source_import({"client_number": "7", "client_name": "Ana Pérez"}, "EMP")
                self.assertEqual(name, "CLIENT")
            get_doc.assert_not_called()

    def test_completed_identifiers_and_new_clients_remain_visible(self):
        row = self.client(); row["national_id"] = ""
        doc = FakeDocument(**{k: v for k, v in row.items() if k != "client_aliases"})
        with patch.object(registry, "load_client_index", return_value=[row]), \
             patch.object(registry.frappe.db, "exists", return_value=True), \
             patch.object(registry.frappe, "get_doc", return_value=doc):
            index = registry.ClientIndex()
            index.ensure_from_source_import({"client_number": "7", "national_id": "ID7"}, "EMP")
            self.assertEqual(index.ensure_from_source_import({"national_id": "ID7"}, "EMP")[0], "CLIENT")
            self.assertEqual(index.ensure_from_source_import({"national_id": "ID7"}, "OTHER")[0], "")
            added = {**self.client(), "name": "NEW", "client_number": "8", "national_id": "ID8"}
            index.records.append(added)
            self.assertEqual(index.linked_client("NEW"), added)
            self.assertEqual(index.ensure_from_source_import({"national_id": "ID8"}, "EMP")[0], "NEW")

    def test_non_application_block_does_not_load_any_catalog(self):
        records = [{"event_type": "Deposito"}]
        with patch.object(registry, "ClientIndex") as index, patch.object(registry.frappe, "get_all") as query:
            self.assertIs(registry.enrich_source_import_clients(records), records)
            index.assert_not_called()
            query.assert_not_called()
