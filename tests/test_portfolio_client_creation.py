"""Client registry behavior for accounting-import portfolio matches."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from credinomina_reconciliation import client_registry


class FakeDocument:
    def __init__(self, name="", **values):
        self.name = name
        self.values = values
        self.saved = False
        self.inserted = False

    def get(self, fieldname):
        return self.values.get(fieldname)

    def set(self, fieldname, value):
        self.values[fieldname] = value

    def save(self, **kwargs):
        self.saved = True

    def insert(self, **kwargs):
        self.inserted = True
        self.name = "CN-CLIENT-NEW"
        return self


class PortfolioClientCreationTests(unittest.TestCase):
    def test_missing_client_is_created_with_siaf_number_and_validated_employer(self):
        document = FakeDocument()
        with (
            patch.object(client_registry, "load_client_index", return_value=[]),
            patch.dict(
                client_registry.frappe.__dict__,
                {"db": SimpleNamespace(exists=lambda *_args, **_kwargs: True)},
            ),
            patch.object(client_registry.frappe, "get_doc", return_value=document) as get_doc,
        ):
            index = client_registry.ClientIndex()
            name, status = index.ensure_from_portfolio(
                {
                    "client_name": "Ana Pérez",
                    "client_number": "SIAF-7",
                    "national_id": "001-010190-0001A",
                },
                "EMP-1",
            )

        self.assertEqual(name, "CN-CLIENT-NEW")
        self.assertIn("creado", status)
        self.assertTrue(document.inserted)
        inserted_doc = get_doc.call_args.args[0]
        self.assertEqual(inserted_doc["client_number"], "SIAF-7")
        self.assertEqual(inserted_doc["employer"], "EMP-1")
        self.assertEqual(index.records[0]["client_number"], "SIAF-7")

    def test_unvalidated_employer_never_creates_client(self):
        with (
            patch.object(client_registry, "load_client_index", return_value=[]),
            patch.dict(
                client_registry.frappe.__dict__,
                {"db": SimpleNamespace(exists=lambda *_args, **_kwargs: False)},
            ),
            patch.object(client_registry.frappe, "get_doc") as get_doc,
        ):
            index = client_registry.ClientIndex()
            name, status = index.ensure_from_portfolio(
                {"client_name": "Ana Pérez", "client_number": "SIAF-7"},
                "EMP-UNKNOWN",
            )

        self.assertFalse(name)
        self.assertIn("empresa", status)
        get_doc.assert_not_called()

    def test_existing_portfolio_link_prevents_duplicate_across_migrated_and_siaf_numbers(self):
        existing = {
            "name": "CN-CLIENT-7",
            "employer": "EMP-1",
            "client_name": "Ana Pérez",
            "client_number": "MIG-7",
            "employee_number": "",
            "national_id": "",
            "client_aliases": [],
        }
        document = FakeDocument(
            name="CN-CLIENT-7", client_number="MIG-7", national_id=""
        )
        with (
            patch.object(client_registry, "load_client_index", return_value=[existing]),
            patch.dict(
                client_registry.frappe.__dict__,
                {"db": SimpleNamespace(exists=lambda *_args, **_kwargs: True)},
            ),
            patch.object(client_registry.frappe, "get_doc", return_value=document) as get_doc,
        ):
            index = client_registry.ClientIndex()
            name, status = index.ensure_from_portfolio(
                {
                    "client_name": "Ana Pérez",
                    "client_number": "SIAF-7",
                    "national_id": "001-010190-0001A",
                    "portfolio_client": "CN-CLIENT-7",
                },
                "EMP-1",
            )

        self.assertEqual(name, "CN-CLIENT-7")
        self.assertEqual(status, "Cliente existente")
        self.assertEqual(existing["client_number"], "MIG-7")
        self.assertEqual(existing["national_id"], "001-010190-0001A")
        self.assertTrue(document.saved)
        get_doc.assert_called_once_with("CN Client", "CN-CLIENT-7")

    def test_conflicting_existing_identifier_is_not_duplicated(self):
        existing = {
            "name": "CN-CLIENT-OTHER",
            "employer": "EMP-2",
            "client_name": "Luis Ruiz",
            "client_number": "SIAF-7",
            "employee_number": "",
            "national_id": "002-020290-0002B",
            "client_aliases": [],
        }
        with (
            patch.object(client_registry, "load_client_index", return_value=[existing]),
            patch.dict(
                client_registry.frappe.__dict__,
                {"db": SimpleNamespace(exists=lambda *_args, **_kwargs: True)},
            ),
            patch.object(client_registry.frappe, "get_doc") as get_doc,
        ):
            index = client_registry.ClientIndex()
            name, status = index.ensure_from_portfolio(
                {
                    "client_name": "Ana Pérez",
                    "client_number": "SIAF-7",
                    "national_id": "001-010190-0001A",
                },
                "EMP-1",
            )

        self.assertFalse(name)
        self.assertIn("Revisar", status)
        get_doc.assert_not_called()
        self.assertEqual(len(index.records), 1)


if __name__ == "__main__":
    unittest.main()
