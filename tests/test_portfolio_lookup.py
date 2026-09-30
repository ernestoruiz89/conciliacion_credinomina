import unittest
from types import SimpleNamespace
from unittest.mock import patch

from credinomina_reconciliation import credit_portfolio as portfolio


def client(name, number, employer="E1"):
    return dict(name=name, client_number=number, employer=employer,
                client_name=name, national_id="ID-" + number, client_aliases=[])


class PortfolioLookupTests(unittest.TestCase):
    def test_exact_alias_ambiguity_and_employer_checks(self):
        ana = client("Ana Pérez", "7")
        ana["client_aliases"] = ["Pérez Ana", "PEREZ ANA"]
        other = client("Ana Pérez", "8", "E2")
        other["name"] = "C8"
        lookup = portfolio.PortfolioClientLookup([ana, other])
        cases = [
            ({"client_number_core": "0007"}, "E1", ana, "Cliente identificado"),
            ({"client_number_core": "7"}, "E2", ana, "Empresa no coincide"),
            ({"client_number_core": "7", "national_id": "ID-8"}, "E1", None, "Identificación ambigua"),
            ({"client_number_core": "7", "client_number_migrated": "8"}, "E1", None, "Identificación ambigua"),
            ({"client_name": "Pérez Ana"}, "E1", ana, "Cliente identificado por nombre"),
            ({"client_name": "Ana Pérez"}, "", None, "Nombre ambiguo"),
            ({"client_name": "Ana Pérez"}, "E2", other, "Cliente identificado por nombre"),
            ({"client_name": "Desconocido"}, "E1", None, "Cliente no registrado"),
            ({}, "E1", None, "Cliente no identificado"),
        ]
        for row, employer, expected, state in cases:
            with self.subTest(row=row, employer=employer):
                actual, _, status = portfolio._client_for_portfolio_row(row, lookup, employer)
                self.assertEqual(actual, expected)
                self.assertEqual(status, state)

    def test_catalog_normalization_is_not_repeated_for_each_credit(self):
        clients = [client(f"Cliente {i}", str(i)) for i in range(1000)]
        lookup = portfolio.PortfolioClientLookup(clients)
        with patch.object(portfolio, "canonical_identifier", wraps=portfolio.canonical_identifier) as normalize:
            for i in range(2000):
                found, _, status = portfolio._client_for_portfolio_row(
                    {"client_number_core": str(i % 1000)}, lookup, "E1")
                self.assertIsNotNone(found)
                self.assertEqual(status, "Cliente identificado")
            self.assertLessEqual(normalize.call_count, 8000)

    def test_duplicate_numbers_still_require_review(self):
        lookup = portfolio.PortfolioClientLookup([client("A", "7"), client("B", "7")])
        found, _, status = portfolio._client_for_portfolio_row({"client_number_core": "7"}, lookup, "E1")
        self.assertIsNone(found)
        self.assertEqual(status, "Identificación ambigua")


class PortfolioMasterCreationTests(unittest.TestCase):
    def analyze(self, rows, clients=(), employers=()):
        inserted = []

        def get_doc(values):
            def insert(**kwargs):
                self.assertEqual(kwargs, {"ignore_permissions": True})
                record = dict(values)
                record["name"] = record.get("client_number") or record["employer_name"]
                inserted.append(record)
                return SimpleNamespace(name=record["name"], as_dict=lambda: record)
            return SimpleNamespace(insert=insert)

        created = {}
        with patch.object(portfolio, "load_client_index", return_value=list(clients)), \
             patch.object(portfolio.frappe, "get_all", return_value=list(employers)), \
             patch.object(portfolio, "attach_employer_aliases"), \
             patch.object(portfolio.frappe, "get_doc", side_effect=get_doc):
            portfolio.analyze_portfolio_rows(rows, created=created)
        return inserted, created

    def row(self, **kwargs):
        return dict(employer_text="Empresa Nueva", client_name="Ana Pérez",
                    client_number_core="123", national_id="ID123",
                    credit_number="100-1", credit_status="Corriente", **kwargs)

    def test_create_once_for_multiple_credits_and_use_siaf(self):
        rows = [self.row(client_number_migrated="999", is_convenio="No"), self.row()]
        inserted, created = self.analyze(rows)
        self.assertEqual(created, {"employers": 1, "clients": 1})
        self.assertEqual(inserted[0]["employer_code"], "Empresa Nueva")
        self.assertEqual(inserted[1]["client_number"], "123")
        self.assertEqual(inserted[1]["national_id"], "ID123")
        self.assertTrue(all(r["matched_client"] == "123" for r in rows))
        self.assertTrue(all(r["validation_status"] == "Cliente y empresa validados" for r in rows))

    def test_empty_and_na_do_not_create(self):
        rows = [self.row() | {"employer_text": value, "is_convenio": "Si"}
                for value in (None, "", "  ", "N/A", " n/a ", "N / A", "#N/A")]
        inserted, created = self.analyze(rows)
        self.assertEqual(inserted, [])
        self.assertEqual(created, {"employers": 0, "clients": 0})
        self.assertTrue(all(r["employer_match_status"] == "No es convenio" for r in rows))

    def test_existing_company_alias_and_client_are_reused(self):
        employers = [dict(name="E1", employer_name="Empresa Real", employer_code="01",
                          aliases=["Empresa Nueva"])]
        rows = [self.row()]
        inserted, _ = self.analyze(rows, [client("Ana", "123")], employers)
        self.assertEqual(inserted, [])
        self.assertEqual(rows[0]["employer"], "E1")
        self.assertEqual(rows[0]["matched_client"], "Ana")

    def test_other_company_and_conflicting_ids_are_not_reassigned(self):
        employers = [dict(name="E2", employer_name="Empresa Nueva", employer_code="02")]
        rows = [self.row(), self.row() | {"national_id": "ID-456"}]
        inserted, _ = self.analyze(rows, [client("Ana", "123"), client("B", "456")], employers)
        self.assertEqual(inserted, [])
        self.assertEqual(rows[0]["validation_status"], "Cliente pertenece a otra empresa")
        self.assertEqual(rows[1]["validation_status"], "Identificación ambigua")

    def test_missing_siaf_or_name_does_not_invent_a_client(self):
        rows = [self.row() | {"client_number_core": "", "client_number_migrated": "999"},
                self.row() | {"client_name": ""}]
        inserted, created = self.analyze(rows)
        self.assertEqual(created, {"employers": 1, "clients": 0})
        self.assertEqual(len(inserted), 1)
        self.assertTrue(all("No creado" in r["validation_status"] for r in rows))

    def test_ambiguous_employer_alias_does_not_create(self):
        employers = [dict(name=name, aliases=["Empresa Nueva"]) for name in ("E1", "E2")]
        rows = [self.row()]
        inserted, _ = self.analyze(rows, employers=employers)
        self.assertEqual(inserted, [])
        self.assertEqual(rows[0]["employer_match_status"], "Empresa ambigua")
