import json
import unittest
from datetime import date
from unittest.mock import Mock, patch

from credinomina_reconciliation import client_credit as credit
from credinomina_reconciliation.detail_balances import update_detail_balances
from credinomina_reconciliation.control_deposits import build_cash_deposits
from credinomina_reconciliation.complementary_compensation import _eligible
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import CNRemittanceAllocation


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def set(self, key, value):
        self[key] = value


class ClientCreditTests(unittest.TestCase):
    def test_credit_reduces_row_pending_without_becoming_a_payment(self):
        row = Row(name="R", amount_usd=110, matched_targets='[{"claim_id":"H:A", "amount_usd":100}]', match_status="Conciliada")
        doc = Row(doctype="CN Remittance Allocation", name="D", docstatus=1, detail_rows=[row], targets=[])
        credits = [Row(credit_detail_row="R", amount_usd=10, result=credit.RESULT)]
        with patch.object(credit, "load_credits", return_value=credits):
            update_detail_balances(doc)
        self.assertEqual((row.linked_usd, row.client_credit_usd, row.pending_usd), (100, 10, 0))
        self.assertEqual(row.match_status, "Conciliada")
        self.assertEqual(credit.row_credit_amounts([Row(credit_detail_row="OTHER", amount_usd=10)], [row]), {})

    def test_only_confirmed_documented_credit_explains_pending(self):
        row = Row(name="R", amount_usd=110, matched_targets='[{"amount_usd":100}]')
        doc = Row(doctype="CN Remittance Allocation", name="D", docstatus=1, detail_rows=[row], targets=[])
        with patch.object(credit, "load_credits", return_value=[Row(credit_detail_row="R", amount_usd=10, result="Excede saldo sin distribuir")]):
            update_detail_balances(doc)
        self.assertEqual((row.client_credit_usd, row.pending_usd), (0, 10))

    def test_client_credit_is_neither_target_nor_offset(self):
        item = Row(name="C", docstatus=1, category=credit.CATEGORY, amount_usd=10)
        with patch.object(credit.frappe, "db", Mock(get_value=Mock(return_value=item))), patch.object(credit.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                CNRemittanceAllocation._validate_target(Row(complementary_item="C", amount_usd=10))
            with self.assertRaises(ValueError):
                _eligible(item)

    def test_category_and_management_are_server_owned(self):
        previous = Row(docstatus=1, category=credit.CATEGORY, credit_resolved_usd=4, credit_pending_usd=6, credit_management_status="Parcialmente resuelto", credit_history='[{"importe_usd":4}]')
        doc = Row(previous, credit_history="[]", credit_resolved_usd=0, credit_pending_usd=10)
        credit.guard_credit_category(doc, previous)
        self.assertEqual(doc.credit_resolved_usd, 4)
        self.assertEqual(doc.credit_history, previous.credit_history)
        doc.category = "Otros ingresos"
        with patch.object(credit.frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
            credit.guard_credit_category(doc, previous)

    def test_cancel_after_management_is_blocked(self):
        with patch.object(credit.frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
            credit.guard_cancel(Row(category=credit.CATEGORY, credit_resolved_usd=0.01))
        credit.guard_cancel(Row(category=credit.CATEGORY, credit_resolved_usd=0))

    def test_deposit_detail_and_financial_identity_are_preserved(self):
        old = Row(name="D", doctype="CN Remittance Allocation", docstatus=1, employer="EMP", deposit_currency="USD", deposit_amount=110,
                  fx_rate=1, deposit_date="2025-05-10", detail_rows=[Row(name="R", client="C", deducted_usd=110)])
        for changes in ({"deposit_amount": 100}, {"employer": "OTHER"}, {"detail_rows": []}, {"detail_rows": [Row(name="R", client="OTHER", deducted_usd=110)]}):
            with patch.object(credit, "load_credits", return_value=[Row(credit_detail_row="R")]), patch.object(credit.frappe, "throw", side_effect=ValueError):
                with self.assertRaises(ValueError):
                    credit.guard_deposit_changes(Row(old, **changes), old)
        with patch.object(credit, "load_credits", return_value=[Row(credit_detail_row="R")]):
            credit.guard_deposit_changes(Row(old, notes="Gestionado"), old)
        with patch.object(credit, "load_credits", return_value=[Row(credit_detail_row="R")]), patch.object(credit.frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
            credit.guard_detail_replacement(old)

    def test_documented_customer_excess_is_settled_cash_not_an_application(self):
        deposit = Row(name="D", amount_usd=110, allocated_usd=100, justified_surplus_usd=10,
                      result="Conciliado con saldo a favor del cliente", allocation_detail=[{"tipo":"Aplicacion historica", "importe_usd":100}])
        items = [Row(name="CREDIT", registered_deposit="D", amount_usd=10, credit_pending_usd=6, client_name="Ana",
                     client_number="1", loan_number="1-1", credit_management_status="Parcialmente resuelto")]
        result, = build_cash_deposits([deposit], client_credits=items)
        self.assertEqual((result["credits_usd"], result["client_credit_usd"], result["client_credit_pending_usd"]), (100, 10, 6))
        self.assertTrue(result["settled"] and not result["needs_review"])
        self.assertEqual(sum(row["amount_usd"] for row in result["destinations"]), 110)
        self.assertEqual(result["destinations"][-1]["people"][0]["client_name"], "Ana")

    def test_client_and_company_credits_share_one_cash_capacity(self):
        allocation = {"deposit_meta":{"D":{"employer":"EMP", "account":Row(allocation_reason=""), "bank":Row(allocation_reason="")}},
                      "registered_ids":{"DEP":"D"}, "deposit_remaining":{"D":10}}
        items = [Row(name="C1", category=credit.CATEGORY, registered_deposit="DEP", employer="EMP", amount_usd=7),
                 Row(name="C2", category="Saldo a favor de la empresa", registered_deposit="DEP", employer="EMP", amount_usd=7)]
        with patch.object(engine.frappe, "db", Mock()) as db:
            engine._classify_surplus(allocation, items)
        self.assertEqual(allocation["client_credit_totals"], {"D":7})
        self.assertEqual(db.set_value.call_args.args[3], "Excede saldo sin distribuir")
        self.assertEqual(allocation["deposit_meta"]["D"]["account"].unclassified_usd, 3)


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.item = Row(doctype="CN Complementary Item", name="C", docstatus=1, category=credit.CATEGORY, result=credit.RESULT,
            amount_usd=10, credit_resolved_usd=4, credit_history='[{"importe_usd":4}]', modified="stamp", posting_date="2025-05-10",
            check_permission=Mock(), add_comment=Mock(), clear_cache=Mock())
        self.args = dict(item_name="C", modified="stamp", treatment="Devolución", amount_usd=6, event_date="2025-05-20", reference="REC", support_file="/private/files/proof.pdf")
        self.db = Mock()
        for patcher in (patch.object(credit.frappe, "get_doc", side_effect=lambda dt, name: self.item if dt == "CN Complementary Item" else Row(check_permission=Mock())),
                        patch.object(credit.frappe, "db", self.db), patch.object(credit.frappe, "get_all", return_value=["FILE"]),
                        patch.object(credit.frappe, "throw", side_effect=ValueError), patch.dict(credit.frappe.__dict__, {"session":Row(user="Operator")}),
                        patch.object(credit, "now_datetime", return_value="2026-10-02 12:00:00")):
            patcher.start(); self.addCleanup(patcher.stop)
        original_getdate = credit.getdate
        patcher = patch.object(credit, "getdate", side_effect=lambda value=None: original_getdate(value) if value else date(2026, 10, 2))
        patcher.start(); self.addCleanup(patcher.stop)

    def test_management_records_proof_and_does_not_reconcile_or_post(self):
        result = credit.record_management(**self.args)
        self.assertEqual((result["status"], result["pending_usd"]), ("Resuelto", 0))
        values = self.db.set_value.call_args.args[2]
        history = json.loads(values["credit_history"])
        self.assertEqual(len(history), 2)
        self.assertEqual((history[-1]["importe_usd"], history[-1]["referencia"], history[-1]["usuario"]), (6, "REC", "Operator"))
        self.assertEqual(set(values), set(credit.MANAGED_FIELDS))
        self.item.check_permission.assert_any_call("write")
        self.item.check_permission.assert_any_call("submit")

    def test_invalid_management_is_rejected(self):
        for changes in ({"amount_usd":7}, {"amount_usd":0}, {"amount_usd":-1}, {"modified":"old"}, {"reference":""},
                        {"support_file":""}, {"event_date":"2025-05-09"}, {"event_date":"2999-01-01"}, {"treatment":"Unknown"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                credit.record_management(**(self.args | changes))
        self.db.set_value.assert_not_called()

    def test_support_must_belong_to_item(self):
        with patch.object(credit.frappe, "get_all", return_value=[]), self.assertRaises(ValueError):
            credit.record_management(**self.args)
        self.db.set_value.assert_not_called()

    def test_permissions_checked_and_draft_cancelled_or_unclassified_rejected(self):
        self.item.check_permission.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            credit.record_management(**self.args)
        self.item.check_permission.side_effect = None
        for status, result in ((0, credit.RESULT), (2, credit.RESULT), (1,"Excede saldo sin distribuir")):
            self.item.docstatus, self.item.result = status, result
            with self.assertRaises(ValueError):
                credit.record_management(**self.args)
        self.db.set_value.assert_not_called()
