"""Validate accounting movements against the selected monthly portfolio cut."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from credinomina_reconciliation import credit_portfolio


class Row(dict):
    """Small frappe._dict stand-in for isolated unit tests."""

    __getattr__ = dict.__getitem__


def portfolio_row(**overrides):
    values = {
        "name": "PORT-ROW-1",
        "credit_number": "CR-100",
        "client_number_migrated": "0007",
        "client_number_core": "7",
        "client_name": "Ana Pérez",
        "credit_status": "Corriente",
        "credit_lifecycle": "Activo",
        "employer_text": "Empresa Norte",
        "employer": "Empresa Norte",
        "employer_match_status": "Empresa identificada",
        "national_id": "001-010190-0001A",
        "is_convenio": "Sí",
        "matched_client": "CLI-7",
        "client_match_status": "Identificador exacto",
        "validation_status": "Cliente y empresa validados",
    }
    values.update(overrides)
    return Row(values)


class FakeClientIndex:
    def ensure_from_portfolio(self, record, employer):
        if record["client_number"] == "SIAF-NEW":
            return "CN-CLIENT-NEW", "Cliente creado automáticamente desde cartera"
        return "CLI-7", "Cliente existente"


class CreditPortfolioEnrichmentTests(unittest.TestCase):
    def enrich(self, movement, rows, register_clients=True):
        with (
            patch.dict(
                credit_portfolio.frappe.__dict__,
                {"db": SimpleNamespace(get_value=lambda *_args, **_kwargs: Row(
                    name="SNAP-2026-09",
                    report_date="2026-09-30",
                    status="Importado",
                ))},
            ),
            patch.object(
                credit_portfolio.frappe,
                "get_all",
                side_effect=[rows, []],
            ),
            patch.object(
                credit_portfolio,
                "employer_alias_index",
                return_value=({}, set()),
            ),
            patch.object(
                credit_portfolio,
                "ClientIndex",
                return_value=FakeClientIndex(),
            ),
        ):
            return credit_portfolio.enrich_accounting_records(
                [movement], selected_snapshot="SNAP-2026-09", register_clients=register_clients
            )[0]

    def test_bulk_preview_enriches_without_creating_clients(self):
        with patch.object(credit_portfolio, "_register_portfolio_client") as register:
            result = self.enrich(
                {"event_type": "Aplicacion", "loan_number": "CR-100"},
                [portfolio_row()], register_clients=False,
            )
        register.assert_not_called()
        self.assertEqual(result["client_number"], "7")
        self.assertEqual(result["portfolio_employer"], "Empresa Norte")

    def test_credit_resolves_company_without_requiring_free_text_alias(self):
        result = self.enrich({"event_type": "Aplicacion", "loan_number": "13375",
                             "employer_text": "Texto contable no registrado"},
                            [portfolio_row(credit_number="13375-1")], register_clients=False)
        self.assertEqual(result["portfolio_employer"], "Empresa Norte")
        self.assertEqual(result["portfolio_validation_status"], "Cliente y empresa validados")
        self.assertEqual(result["portfolio_snapshot_used"], "SNAP-2026-09")
        self.assertEqual(result["employer_text"], "Texto contable no registrado")

    def test_different_credit_cycle_does_not_match(self):
        result = self.enrich({"event_type": "Aplicacion", "loan_number": "13375-2"},
                            [portfolio_row(credit_number="13375-1")], register_clients=False)
        self.assertEqual(result["portfolio_validation_status"], "Crédito no encontrado en el corte")
        self.assertNotIn("portfolio_employer", result)

    def test_credit_and_client_number_both_validate_and_ignore_leading_zeroes(self):
        movement = {
            "event_type": "Aplicacion",
            "loan_number": "CR-100",
            "client_number": "7",
        }

        result = self.enrich(movement, [portfolio_row()])

        self.assertEqual(result["portfolio_validation_status"], "Cliente y empresa validados")
        self.assertEqual(result["portfolio_client_name"], "Ana Pérez")
        self.assertEqual(result["client_number"], "7")

    def test_missing_client_number_is_filled_from_siaf_number_on_credit_match(self):
        movement = {
            "event_type": "Aplicacion",
            "loan_number": "CR-100",
        }

        result = self.enrich(
            movement,
            [portfolio_row(client_number_migrated="MIG-7", client_number_core="SIAF-7")],
        )

        self.assertEqual(result["client_number"], "SIAF-7")
        self.assertEqual(result["portfolio_validation_status"], "Cliente y empresa validados")

    def test_client_number_conflict_with_matched_credit_is_visible(self):
        movement = {
            "event_type": "Aplicacion",
            "loan_number": "CR-100",
            "client_number": "99",
        }

        result = self.enrich(movement, [portfolio_row()])

        self.assertEqual(
            result["portfolio_validation_status"],
            "Número de cliente del movimiento no coincide con el crédito en cartera",
        )
        self.assertEqual(
            result["client_registry_status"],
            "Revisar: número de cliente del movimiento no coincide con SIAF",
        )

    def test_siaf_number_is_the_client_number_used_for_validation(self):
        movement = {
            "event_type": "Aplicacion",
            "loan_number": "CR-100",
            "client_number": "MIG-7",
        }

        result = self.enrich(
            movement,
            [portfolio_row(client_number_migrated="MIG-7", client_number_core="SIAF-7")],
        )

        self.assertEqual(
            result["portfolio_validation_status"],
            "Número de cliente del movimiento no coincide con el crédito en cartera",
        )

    def test_missing_client_is_created_and_linked_from_validated_portfolio_row(self):
        movement = {
            "event_type": "Aplicacion",
            "loan_number": "CR-NEW",
        }

        result = self.enrich(
            movement,
            [portfolio_row(
                name="PORT-ROW-NEW",
                credit_number="CR-NEW",
                client_number_core="SIAF-NEW",
                client_name="Cliente Nuevo",
                national_id="001-010190-0002B",
                matched_client="",
            )],
        )

        self.assertEqual(result["client_number"], "SIAF-NEW")
        self.assertEqual(result["portfolio_client"], "CN-CLIENT-NEW")
        self.assertEqual(
            result["client_registry_status"],
            "Cliente creado automáticamente desde cartera",
        )

    def test_client_number_identifies_customer_when_credit_number_is_missing(self):
        movement = {
            "event_type": "Aplicacion",
            "client_number": "7",
        }
        rows = [
            portfolio_row(credit_number="CR-100"),
            portfolio_row(name="PORT-ROW-2", credit_number="CR-101"),
        ]

        result = self.enrich(movement, rows)

        self.assertEqual(result["client_name"], "Ana Pérez")
        self.assertEqual(result["national_id"], "001-010190-0001A")
        self.assertEqual(result["portfolio_client"], "CLI-7")
        self.assertEqual(result["portfolio_employer"], "Empresa Norte")
        self.assertEqual(
            result["portfolio_validation_status"],
            "Cliente y empresa validados por número de cliente; falta número de crédito",
        )
        self.assertNotIn("portfolio_credit_status", result)

    def test_client_number_without_credit_match_still_validates_customer(self):
        movement = {
            "event_type": "Aplicacion",
            "loan_number": "CR-MISSING",
            "client_number": "7",
        }

        result = self.enrich(movement, [portfolio_row()])

        self.assertEqual(result["portfolio_client"], "CLI-7")
        self.assertEqual(
            result["portfolio_validation_status"],
            "Crédito no encontrado en el corte; cliente identificado por número de cliente",
        )

    def test_ambiguous_client_number_is_not_assigned(self):
        movement = {
            "event_type": "Aplicacion",
            "client_number": "7",
        }
        rows = [
            portfolio_row(),
            portfolio_row(
                name="PORT-ROW-2",
                credit_number="CR-200",
                matched_client="CLI-OTHER",
                client_name="Otra persona",
                national_id="002-020290-0002B",
            ),
        ]

        result = self.enrich(movement, rows)

        self.assertEqual(result["portfolio_validation_status"], "Número de cliente ambiguo en el corte")
        self.assertNotIn("portfolio_client", result)


if __name__ == "__main__":
    unittest.main()
