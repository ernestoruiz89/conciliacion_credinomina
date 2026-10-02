import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.allocation import allocate_cash
from credinomina_reconciliation.complementary_distribution import validate_distribution
from credinomina_reconciliation.paying_employers import allowed_employers, reconciliation_companies
from credinomina_reconciliation.remittance_detail import manual_detail_targets, suggest_detail_targets
from credinomina_reconciliation.control_deposits import build_cash_deposits


class GenericComplementaryDistributionTests(unittest.TestCase):
    def setUp(self):
        self.claim = dict(id="X:G", kind="X", group="LALA", groups=["LALA", "CBC"], manual_only=True,
                          amount_usd=500, references=["REF"])
        self.deposits = [dict(id="LALA", group="LALA", amount_usd=200, reference="REF"),
                         dict(id="CBC", group="CBC", amount_usd=300, reference="REF")]

    def test_two_companies_share_one_capacity_and_never_gain_payer_rights(self):
        result = allocate_cash(self.deposits, [self.claim], [
            dict(id="A", deposit_id="LALA", claim_id="X:G", amount_usd=200, group="LALA"),
            dict(id="B", deposit_id="CBC", claim_id="X:G", amount_usd=300, group="CBC"),
        ])
        self.assertEqual(result["claim_remaining"], {"X:G": 0})
        self.assertEqual(result["deposit_remaining"], {"LALA": 0, "CBC": 0})
        self.assertEqual([row["group"] for row in result["allocations"]], ["LALA", "CBC"])
        self.assertEqual(reconciliation_companies("LALA", {}, [{"LALA", "CBC"}]), ["CBC", "LALA"])
        self.assertEqual(allowed_employers("LALA", {}), {"LALA"})

    def test_generic_is_never_guessed_from_name_credit_or_reference(self):
        row = dict(client_name="Ana", loan_number="1-1", employer="LALA")
        self.assertEqual(suggest_detail_targets(row, [self.claim], 500, "LALA")[0], [])
        result = allocate_cash(self.deposits, [self.claim])
        self.assertEqual(result["allocations"], [])

    def test_negative_generic_is_shared_and_net_cash_still_balances(self):
        claims = [self.claim | {"amount_usd": -500}, dict(id="H:A", group="LALA", amount_usd=300), dict(id="H:B", group="CBC", amount_usd=500)]
        deposits = [self.deposits[0] | {"amount_usd": 100}, self.deposits[1] | {"amount_usd": 200}]
        result = allocate_cash(deposits, claims, [
            dict(id="A", deposit_id="LALA", claim_id="X:G", amount_usd=-200, group="LALA"),
            dict(id="AA", deposit_id="LALA", claim_id="H:A", amount_usd=300),
            dict(id="B", deposit_id="CBC", claim_id="X:G", amount_usd=-300, group="CBC"),
            dict(id="BB", deposit_id="CBC", claim_id="H:B", amount_usd=500),
        ])
        self.assertTrue(all(value == "Aplicada" for value in result["instruction_results"].values()))
        self.assertEqual(result["deposit_remaining"], {"LALA": 0, "CBC": 0})
        self.assertTrue(all(value == 0 for value in result["claim_remaining"].values()))
        self.assertEqual(sum(row["amount_usd"] for row in result["allocations"]), 300)

    def test_forged_company_and_one_cent_over_capacity_are_rejected(self):
        for company, amount, error in [("OTHER", 300, "Empresa no autorizada"), ("CBC", 300.01, "Excede cobranza")]:
            deposits = [self.deposits[0], self.deposits[1] | {"amount_usd": 301}]
            result = allocate_cash(deposits, [self.claim], [
                dict(id="A", deposit_id="LALA", claim_id="X:G", amount_usd=200, group="LALA"),
                dict(id="B", deposit_id="CBC", claim_id="X:G", amount_usd=amount, group=company),
            ])
            self.assertEqual(result["instruction_results"]["B"], error)
            self.assertEqual(result["claim_remaining"]["X:G"], 300)

    def test_manual_generic_requires_row_company_when_ambiguous_and_respects_period(self):
        instruction = [dict(id="T", claim_id="X:G", amount_usd=200)]
        self.assertFalse(manual_detail_targets({}, [self.claim], instruction, 200, "LALA", allowed_groups=["LALA", "CBC"])[0])
        row = dict(employer="CBC", client_name="Ana", client_number="1", loan_number="1-1")
        targets, _ = manual_detail_targets(row, [self.claim], instruction, 200, "LALA", "APRIL", ["LALA", "CBC"])
        self.assertEqual(targets[0]["group"], "CBC")
        self.assertFalse(manual_detail_targets(row, [self.claim | {"period": "OTHER"}], instruction, 200, "LALA", "APRIL", ["LALA", "CBC"])[0])

    def test_generic_validation_cannot_disguise_an_application_adjustment_or_single_client(self):
        base = frappe._dict(generic_distribution=1, employer="LALA", category="Ajuste de conciliación")
        with patch.object(frappe, "_", side_effect=lambda value, **kwargs: value), patch.object(frappe, "throw", side_effect=ValueError):
            validate_distribution(base)
            for values in [{"category": "Ajuste de aplicación"}, {"client_number": "1"},
                           {"distribution_companies": [frappe._dict(employer="LALA")]},
                           {"period": "APRIL", "distribution_companies": [frappe._dict(employer="CBC")]}]:
                with self.assertRaises(ValueError):
                    validate_distribution(frappe._dict(base | values))

    def test_cash_overview_keeps_manual_split_people_and_companies(self):
        deposit = dict(name="D", amount_usd=500, allocated_usd=500, result="Conciliado", allocation_detail=[
            dict(tipo="Partida complementaria", partida="G", importe_usd=200, empresa="LALA", fila_detalle="R1", cliente="Ana", nro_cliente="1", credito="1-1"),
            dict(tipo="Partida complementaria", partida="G", importe_usd=300, empresa="CBC", fila_detalle="R2", cliente="Luis", nro_cliente="2", credito="2-1"),
        ])
        row, = build_cash_deposits([deposit], {"G": dict(name="G", category="Ajuste de conciliación", employer="LALA")})
        self.assertEqual(row["other_usd"], 500)
        self.assertEqual([d["employer"] for d in row["destinations"]], ["LALA", "CBC"])
        self.assertEqual([d["people"][0]["amount_usd"] for d in row["destinations"]], [200, 300])
