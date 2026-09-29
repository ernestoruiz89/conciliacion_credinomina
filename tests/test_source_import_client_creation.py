"""Client creation and conflict handling for accounting source imports."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class FakeDocument:
    def __init__(self, name="", **values):
        self.name = name
        self.values = values
        self.aliases = values.get("aliases", [])
        self.inserted = False
        self.saved = False

    def get(self, fieldname):
        if fieldname == "aliases":
            return self.aliases
        return self.values.get(fieldname)

    def set(self, fieldname, value):
        self.values[fieldname] = value

    def append(self, fieldname, value):
        if fieldname == "aliases":
            self.aliases.append(types.SimpleNamespace(**value))

    def insert(self, **_kwargs):
        self.inserted = True
        self.name = "CN-CLIENT-NEW"
        return self

    def save(self, **_kwargs):
        self.saved = True


def load_client_registry(frappe_stub):
    path = ROOT / "credinomina_reconciliation" / "client_registry.py"
    spec = importlib.util.spec_from_file_location("_client_registry_source_import_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"frappe": frappe_stub}):
        spec.loader.exec_module(module)
    return module


class SourceImportClientCreationTests(unittest.TestCase):
    def test_import_resolves_employer_alias_before_creating_client(self):
        created_documents = []

        def get_doc(value, *_args):
            document = FakeDocument(**{key: val for key, val in value.items() if key != "doctype"})
            created_documents.append(document)
            return document

        frappe_stub = types.ModuleType("frappe")
        frappe_stub._ = lambda message: message
        frappe_stub.db = types.SimpleNamespace(
            exists=lambda doctype, name: doctype == "CN Employer" and name == "Empresa Norte"
        )
        frappe_stub.get_doc = get_doc
        frappe_stub.get_all = lambda doctype, **_kwargs: (
            [{"name": "Empresa Norte", "employer_name": "Empresa Norte", "employer_code": "N-01"}]
            if doctype == "CN Employer" else []
        )
        registry = load_client_registry(frappe_stub)
        rows = [{
            "event_type": "Aplicacion",
            "employer_text": "Nombre anterior",
            "client_name": "Ana Pérez",
            "client_number": "SIAF-7",
            "national_id": "001-010190-0001A",
        }]

        with (
            patch.object(registry, "load_client_index", return_value=[]),
            patch.object(
                registry,
                "attach_employer_aliases",
                side_effect=lambda employers: employers[0].update({"aliases": ["Nombre anterior"]}),
            ),
        ):
            registry.enrich_source_import_clients(rows)

        self.assertEqual("Empresa Norte", created_documents[0].values["employer"])
        self.assertEqual("CN-CLIENT-NEW", rows[0]["client"])
        self.assertIn("creado", rows[0]["client_registry_status"])

    def test_existing_client_is_found_by_identifier_when_source_name_is_blank(self):
        existing_document = FakeDocument(
            name="CN-CLIENT-7",
            employer="EMP-1",
            client_name="Ana Pérez",
            client_number="SIAF-7",
            employee_number="",
            national_id="001-010190-0001A",
        )
        frappe_stub = types.ModuleType("frappe")
        frappe_stub._ = lambda message: message
        frappe_stub.db = types.SimpleNamespace(
            exists=lambda doctype, name: doctype == "CN Employer" and name == "EMP-1"
        )
        frappe_stub.get_doc = lambda *_args, **_kwargs: existing_document
        registry = load_client_registry(frappe_stub)
        existing = {
            "name": "CN-CLIENT-7",
            "employer": "EMP-1",
            "client_name": "Ana Pérez",
            "client_number": "SIAF-7",
            "employee_number": "",
            "national_id": "001-010190-0001A",
            "client_aliases": [],
        }

        with patch.object(registry, "load_client_index", return_value=[existing]):
            index = registry.ClientIndex()
            name, status = index.ensure_from_source_import(
                {"client_number": "SIAF-7", "national_id": "001-010190-0001A"},
                "EMP-1",
            )

        self.assertEqual("CN-CLIENT-7", name)
        self.assertEqual("Cliente existente", status)

    def test_missing_client_is_created_and_same_identifier_reuses_it(self):
        existing_document = FakeDocument(
            name="CN-CLIENT-NEW",
            employer="EMP-1",
            client_name="Ana Pérez",
            client_number="SIAF-7",
            employee_number="",
            national_id="001-010190-0001A",
        )
        created_documents = []

        def get_doc(value, *_args):
            if isinstance(value, dict):
                document = FakeDocument(**{key: val for key, val in value.items() if key != "doctype"})
                created_documents.append(document)
                return document
            return existing_document

        frappe_stub = types.ModuleType("frappe")
        frappe_stub._ = lambda message: message
        frappe_stub.db = types.SimpleNamespace(
            exists=lambda doctype, name: doctype == "CN Employer" and name == "EMP-1"
        )
        frappe_stub.get_doc = get_doc
        registry = load_client_registry(frappe_stub)

        with patch.object(registry, "load_client_index", return_value=[]):
            index = registry.ClientIndex()
            first_name, first_status = index.ensure_from_source_import(
                {
                    "client_name": "Ana Pérez",
                    "client_number": "SIAF-7",
                    "national_id": "001-010190-0001A",
                },
                "EMP-1",
            )
            second_name, second_status = index.ensure_from_source_import(
                {
                    "client_name": "Pérez Ana",
                    "client_number": "SIAF-7",
                    "national_id": "001-010190-0001A",
                },
                "EMP-1",
            )

        self.assertEqual("CN-CLIENT-NEW", first_name)
        self.assertIn("creado", first_status)
        self.assertEqual("CN-CLIENT-NEW", second_name)
        self.assertEqual("Cliente existente", second_status)
        self.assertEqual(1, len(created_documents))
        self.assertEqual("EMP-1", created_documents[0].values["employer"])
        self.assertEqual("SIAF-7", created_documents[0].values["client_number"])
        self.assertEqual("001-010190-0001A", created_documents[0].values["national_id"])

    def test_conflicting_identifier_is_flagged_instead_of_creating_duplicate(self):
        frappe_stub = types.ModuleType("frappe")
        frappe_stub._ = lambda message: message
        frappe_stub.db = types.SimpleNamespace(
            exists=lambda doctype, name: doctype == "CN Employer" and name == "EMP-1"
        )
        frappe_stub.get_doc = lambda *_args, **_kwargs: self.fail("No debe crear otro cliente")
        registry = load_client_registry(frappe_stub)
        existing = {
            "name": "CN-CLIENT-OTHER",
            "employer": "EMP-2",
            "client_name": "Luis Ruiz",
            "client_number": "SIAF-7",
            "employee_number": "",
            "national_id": "002-020290-0002B",
            "client_aliases": [],
        }

        with patch.object(registry, "load_client_index", return_value=[existing]):
            index = registry.ClientIndex()
            name, status = index.ensure_from_source_import(
                {
                    "client_name": "Ana Pérez",
                    "client_number": "SIAF-7",
                    "national_id": "001-010190-0001A",
                },
                "EMP-1",
            )

        self.assertFalse(name)
        self.assertIn("Revisar", status)
        self.assertEqual(1, len(index.records))


if __name__ == "__main__":
    unittest.main()
