import csv
import io
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation import accounting_deposits as deposits
from credinomina_reconciliation.accounting_control import build_rows
from credinomina_reconciliation.accounting_types import classify_movement, DEPOSIT, REVIEW
from credinomina_reconciliation.deposit_evidence import extract_deposit
from credinomina_reconciliation.parsers import parse_accounting_movements, apply_accounting_currency_override


def ledger(credit=3662.43, description="C$4394.92 DEPOSITO POR CONVENIO A EN LA CUENTA BAC 6906 C$ EL DIA 04/04/2025", tdoc="12", debit=0):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["Fecha Aplica", "Cuenta Contable", "Descripción Cuenta", "Descripción", "TMov", "TDoc", "No. Cmpte", "No. Ref", "Débito del Mes", "Crédito del Mes", "Empresa"])
    writer.writerow(["2025-04-04", "1602", "Convenios M.E.", description, "'02", "'" + tdoc, "001", "REF", debit, credit, "A"])
    return stream.getvalue().encode()


class DepositTests(unittest.TestCase):
    def test_explicit_classification_and_reversals(self):
        self.assertEqual(classify_movement("'02", "'12", 0, 10)[:2], (DEPOSIT, False))
        for debit, credit, text in [(1, 10, ""), (10, 0, ""), (0, -10, ""), (0, 10, "REVERSIÓN")]:
            self.assertEqual(classify_movement(2, 12, debit, credit, text)[0], REVIEW)
        self.assertEqual(parse_accounting_movements("x.csv", ledger(tdoc="08"))[0]["event_type"], "Ajuste")

    def test_cash_total_separate_from_ledger_credit(self):
        rows = apply_accounting_currency_override(parse_accounting_movements("x.csv", ledger()), "NIO", 36.6243)
        row = rows[0]
        self.assertEqual(row["event_type"], "Deposito")
        self.assertEqual(row["source_credit"], 3662.43)
        self.assertEqual(row["amount_usd"], 100)
        plans = deposits.plan_deposits(rows, [{"name": "A", "employer_name": "A"}])
        self.assertEqual(plans[0]["deposit_amount"], 4394.92)
        self.assertEqual(plans[0]["deposit_usd"], 120)
        self.assertEqual(plans[0]["resolved_employer"], "A")

    def test_cash_usd_accounting_nio(self):
        rows = apply_accounting_currency_override(parse_accounting_movements("x.csv", ledger(description="U$120.00DEPOSITO EN LA CUENTA BAC 6922 U$")), "NIO", 36.6243)
        plan = deposits.plan_deposits(rows, [{"name": "A"}])[0]
        self.assertEqual((plan["deposit_currency"], plan["deposit_amount"], plan["deposit_usd"]), ("USD", 120, 120))
        self.assertEqual(plan["amount_usd"], 100)

    def test_explicit_columns_and_uncertain_bank(self):
        row = extract_deposit({"moneda": "NIO", "dep_en_banco": 500, "no_ref_banco": "BANKREF"}, "C$400 DEPOSITO EN LA CUENTA BANPRO C$")
        self.assertEqual(row["bank_deposit_amount"], 500)
        self.assertEqual(row["bank_deposit_reference"], "BANKREF")
        self.assertEqual(row["bank_name_hint"], "")
        self.assertEqual(deposits._bank_account({**row, "deposit_currency": "NIO"})[0], "")

    def test_bank_unique_match_ambiguous_inactive_and_currency_conflict(self):
        row = {"bank_name_hint": "BAC", "bank_number_hint": "6906", "bank_currency_hint": "NIO", "deposit_currency": "NIO"}
        account = frappe._dict(name="BAC cuenta", account_number="123456906", active=1, currency="NIO")
        with patch.object(frappe, "has_permission", return_value=True), patch.object(frappe, "get_list", return_value=[account]) as query:
            self.assertEqual(deposits._bank_account(row)[0], "BAC cuenta")
            query.return_value = [account, account]
            self.assertEqual(deposits._bank_account(row)[0], "")
            query.return_value = [frappe._dict(account, currency="USD")]
            self.assertEqual(deposits._bank_account(row)[0], "")
            query.return_value = [frappe._dict(account, active=0)]
            self.assertEqual(deposits._bank_account(row)[0], "")

    def test_report_deduplicates_mirror_and_uses_current_deposit_state(self):
        row = apply_accounting_currency_override(parse_accounting_movements("x.csv", ledger()), "NIO", 36.6243)[0]
        cash = {**row, "name": "DEP-4-2025-0001", "source_date": "2025-04-04", "docstatus": 0, "result": "Pendiente",
                "bank_account": "BAC 6906 C$", "employer": "A", "deposit_amount": 4394.92}
        row.update(name="ROW", parent="IMPORT", remittance_allocation=cash["name"])
        for sources, parents in [([], {}), ([row], {"IMPORT": {"name": "IMPORT", "employer": "A"}})]:
            result, _ = build_rows(sources, parents, [], deposits=[cash])
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["credit_nio"], 3662.43)
            self.assertEqual(result[0]["credit_usd"], 100)
            self.assertEqual(result[0]["state"], "Pendiente")
            self.assertEqual(result[0]["remittance_allocation"], cash["name"])
        cash.update(docstatus=1, result="Conciliado")
        self.assertEqual(build_rows([row], parents, [], deposits=[cash])[0][0]["state"], "Conciliado")

    def test_unknown_employer_not_forced(self):
        rows = apply_accounting_currency_override(parse_accounting_movements("x.csv", ledger()), "NIO", 36.6243)
        self.assertEqual(deposits.plan_deposits(rows, [{"name": "B"}], "B")[0]["resolved_employer"], "NO IDENTIFICADA")
