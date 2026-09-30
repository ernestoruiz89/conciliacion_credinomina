import unittest
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
