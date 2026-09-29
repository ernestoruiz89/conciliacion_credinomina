"""Client-number naming and safe migration of the client catalog."""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_client_registry(frappe_stub):
    spec = importlib.util.spec_from_file_location(
        "_client_registry_naming_test", ROOT / "credinomina_reconciliation/client_registry.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"frappe": frappe_stub}):
        spec.loader.exec_module(module)
    return module


class Row(dict):
    __getattr__ = dict.__getitem__


class ClientNamingTests(unittest.TestCase):
    def test_name_uses_required_client_number_without_series(self):
        path = ROOT / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_client/cn_client.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in metadata["fields"]}
        self.assertEqual(metadata["autoname"], "field:client_number")
        self.assertEqual(metadata["naming_rule"], "By fieldname")
        self.assertTrue(fields["client_number"]["reqd"])
        self.assertNotIn("naming_series", fields)

    def migrate(self, clients, links):
        calls = []
        frappe = types.ModuleType("frappe")
        frappe._ = lambda message: message
        frappe.throw = Mock(side_effect=lambda message: (_ for _ in ()).throw(ValueError(message)))
        frappe.db = types.SimpleNamespace(
            table_exists=lambda _dt: True,
            set_value=lambda _dt, name, field, value, **_kw: clients[name].update({field: value}),
        )
        frappe.get_meta = lambda _dt: types.SimpleNamespace(autoname="field:client_number")
        frappe.get_all = lambda *_args, **_kw: [Row(name=name, **row) for name, row in clients.items()]

        def rename(doctype, old, new, **kwargs):
            self.assertEqual(doctype, "CN Client")
            self.assertTrue(kwargs["force"])
            self.assertNotIn("ignore_permissions", kwargs)
            self.assertNotIn(new, clients)
            calls.append((old, new))
            clients[new] = clients.pop(old)
            clients[new]["client_number"] = new  # after_rename hook
            for link in links:
                if link["client"] == old:
                    link["client"] = new

        frappe.rename_doc = rename
        naming = types.ModuleType("frappe.model.naming")
        naming.validate_name = lambda _dt, value: value
        with patch.dict(sys.modules, {"frappe": frappe, "frappe.model.naming": naming}):
            spec = importlib.util.spec_from_file_location(
                "_test_client_number_migration",
                ROOT / "credinomina_reconciliation/patches/v1_0/rename_cn_clients_by_number.py",
            )
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.execute()
            first_call_count = len(calls)
            migration.execute()
            self.assertEqual(len(calls), first_call_count)  # Safe retry.
        return calls

    def test_migration_preserves_numbers_leading_zeros_aliases_and_links(self):
        clients = {"CN-CLIENT-00001": {"client_number": "00123", "aliases": ["Pérez Ana"]}}
        links = [{"client": "CN-CLIENT-00001"}, {"client": "CN-CLIENT-00001"}]
        self.migrate(clients, links)
        self.assertEqual(list(clients), ["00123"])
        self.assertEqual(clients["00123"]["aliases"], ["Pérez Ana"])
        self.assertEqual([link["client"] for link in links], ["00123", "00123"])

    def test_migration_handles_crossed_existing_names(self):
        clients = {"12": {"client_number": "34", "client_name": "Ana"},
                   "34": {"client_number": "12", "client_name": "Luis"}}
        self.migrate(clients, [])
        self.assertEqual(clients["34"]["client_name"], "Ana")
        self.assertEqual(clients["12"]["client_name"], "Luis")

    def test_missing_and_duplicate_numbers_stop_before_any_rename(self):
        for bad_number in ("", "00123"):
            clients = {"OLD-A": {"client_number": "123"}, "OLD-B": {"client_number": bad_number}}
            with self.subTest(number=bad_number), self.assertRaises(ValueError):
                self.migrate(clients, [])
            self.assertEqual(set(clients), {"OLD-A", "OLD-B"})

    def test_import_without_number_keeps_row_without_inventing_a_client(self):
        frappe = types.ModuleType("frappe")
        frappe._ = lambda message: message
        frappe.db = types.SimpleNamespace(exists=lambda *_args: True)
        frappe.get_doc = Mock(side_effect=AssertionError("Must not insert without a number"))
        registry = load_client_registry(frappe)
        record = {"client_name": "Ana Pérez", "national_id": "CED-1"}
        with patch.object(registry, "load_client_index", return_value=[]):
            index = registry.ClientIndex()
            self.assertEqual(index.ensure_from_collection(record, "EMP"), "")
            for method in (index.ensure_from_source_import, index.ensure_from_portfolio):
                name, status = method(record, "EMP")
                self.assertEqual(name, "")
                self.assertIn("falta número", status)
        frappe.get_doc.assert_not_called()

    def test_name_only_import_can_still_link_existing_client(self):
        frappe = types.ModuleType("frappe")
        frappe._ = lambda message: message
        frappe.db = types.SimpleNamespace(exists=lambda *_args: True)
        frappe.get_doc = Mock(side_effect=AssertionError("Must reuse the existing client"))
        registry = load_client_registry(frappe)
        existing = {"name": "00123", "client_number": "00123", "client_name": "Ana Pérez",
                    "client_aliases": [], "employer": "EMP"}
        with patch.object(registry, "load_client_index", return_value=[existing]):
            index = registry.ClientIndex()
            self.assertEqual(index.ensure_from_collection({"client_name": "Ana Pérez"}, "EMP"), "00123")
        frappe.get_doc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
