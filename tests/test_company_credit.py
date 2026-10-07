import unittest
from unittest.mock import Mock, patch

from credinomina_reconciliation import company_credit as credit
from credinomina_reconciliation import client_credit
from credinomina_reconciliation.control_deposits import build_cash_deposits
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as source
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import CNRemittanceAllocation
from credinomina_reconciliation.patches.v1_0 import integrate_deposit_surplus as migration


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class CompanyCreditTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(client_credit, "lock_credit_deposit", side_effect=lambda name: credit.frappe.get_doc("CN Remittance Allocation", name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def document(self, **values):
        return Row({"amount_usd": 10, "registered_deposit": "DEP", "employer": "EMP",
                    "credit_assigned_to": "Administrator", "credit_commitment_date": "2026-10-10",
                    "credit_treatment": "Pendiente de decisión",
                    "description": "Devolver a la empresa", "reason_type": "Error de la empresa", **values})

    def test_valid_credit_derives_deposit_identification(self):
        doc = self.document()
        deposit = Row(name="DEP", amount_usd=100, allocated_usd=80, docstatus=1, deposit_date="2025-05-10", employer="EMP",
                      deposit_reference="REF", deposit_voucher="BANK", check_permission=Mock())
        with patch.object(credit.frappe, "get_doc", return_value=deposit), patch.object(credit.frappe, "db", Mock(sql=Mock(return_value=[]))), patch.object(credit, "ensure_related_periods_open") as guard:
            credit.validate_company_credit(doc)
        self.assertEqual((doc.reference, doc.deposit_voucher), ("REF", "BANK"))
        guard.assert_called_once_with(doc)
        deposit.check_permission.assert_called_once_with("read")
        self.assertEqual((doc.credit_resolved_usd, doc.credit_pending_usd, doc.credit_management_status), (0, 10, "Pendiente"))

    def test_company_credit_respects_other_reservations(self):
        deposit = Row(name="DEP", amount_usd=100, allocated_usd=80, docstatus=1, deposit_date="2025-05-10", employer="EMP", check_permission=Mock())
        with patch.object(credit.frappe, "get_doc", return_value=deposit), patch.object(credit.frappe, "db", Mock(sql=Mock(return_value=[Row(amount_usd=15)]))), patch.object(credit.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                credit.validate_company_credit(self.document())

    def test_invalid_credit_rejected(self):
        for values in ({"amount_usd": 0}, {"amount_usd": -10}, {"client_number": "123"},
                       {"loan_number": "123-1"}, {"installment_number": "1"},
                       {"registered_deposit": None}, {"reason_type": ""}, {"description": "  "},
                       {"credit_client": "CLIENT"},
                       {"credit_assigned_to": ""}, {"credit_commitment_date": ""}, {"credit_treatment": ""}):
            with self.subTest(values=values), patch.object(credit.frappe, "throw", side_effect=ValueError):
                with self.assertRaises(ValueError):
                    credit.validate_company_credit(self.document(**values))

    def test_company_credit_row_is_evidence_and_respects_row_reservations(self):
        row = Row(name="ROW", amount_usd=30, deducted_usd=30, linked_usd=5, client="CLIENT", loan_number="123-1")
        deposit = Row(name="DEP", amount_usd=100, allocated_usd=5, docstatus=1, deposit_date="2025-05-10",
                      employer="EMP", check_permission=Mock(), detail_rows=[row])
        with patch.object(credit.frappe, "get_doc", return_value=deposit), \
             patch.object(credit.frappe, "db", Mock(sql=Mock(return_value=[Row(amount_usd=15, credit_detail_row="ROW")]))), \
             patch.object(credit, "ensure_related_periods_open"), patch.object(credit.frappe, "throw", side_effect=ValueError):
            doc = self.document(credit_detail_row="ROW")
            credit.validate_company_credit(doc)
            self.assertEqual(doc.credit_detail_row, "ROW")
            self.assertFalse(doc.credit_client or doc.client_number or doc.loan_number)
            for values in ({"credit_detail_row": "OTHER"}, {"credit_detail_row": "ROW", "amount_usd": 10.01}):
                with self.subTest(values=values), self.assertRaises(ValueError):
                    credit.validate_company_credit(self.document(**values))

    def test_draft_or_wrong_company_deposit_rejected(self):
        for status, employer in ((0, "EMP"), (2, "EMP"), (1, "OTHER")):
            deposit = Row(docstatus=status, employer=employer, deposit_date="2025-05-10", check_permission=Mock())
            with patch.object(credit.frappe, "get_doc", return_value=deposit), patch.object(credit.frappe, "throw", side_effect=ValueError):
                with self.assertRaises(ValueError):
                    credit.validate_company_credit(self.document())

    def test_company_credit_cannot_be_used_as_payment_target(self):
        target = Row(complementary_item="COMP", amount_usd=10)
        item = Row(docstatus=1, amount_usd=10, category=credit.CATEGORY)
        with patch.object(credit.frappe, "db", Mock(get_value=Mock(return_value=item))), patch.object(credit.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                CNRemittanceAllocation._validate_target(target)

    def test_company_cash_explained_separately_from_follow_up(self):
        deposit = Row(name="DEP", amount_usd=100, allocated_usd=80, justified_surplus_usd=20,
            result="Parcial con saldo a favor", allocation_detail='[{"tipo":"Aplicacion historica","importe_usd":80}]')
        credit_row = Row(name="C", category=credit.CATEGORY, registered_deposit="DEP", amount_usd=20,
            credit_management_status="Parcialmente resuelto", credit_pending_usd=5)
        result, = build_cash_deposits([deposit], client_credits=[credit_row])
        self.assertTrue(result["settled"])
        self.assertEqual(result["credit_management_pending_usd"], 5)
        self.assertEqual(result["client_credit_usd"], 0)
        self.assertEqual(result["company_credit_usd"], 20)
        self.assertEqual(sum(part["amount_usd"] for part in result["destinations"]), 100)

    def classify(self, items, remaining=10):
        account, bank = Row(allocation_reason=""), Row(allocation_reason="")
        allocation = {"deposit_meta": {"D": {"account": account, "bank": bank, "employer": "EMP"}},
                      "registered_ids": {"DEP": "D"}, "deposit_remaining": {"D": remaining}}
        with patch.object(source.frappe, "db", Mock()) as db:
            source._classify_surplus(allocation, items)
        return account, db

    def test_credit_documents_unallocated_money_not_a_loan_payment(self):
        account, db = self.classify([self.document(name="COMP")])
        self.assertEqual((account.justified_surplus_usd, account.unclassified_usd), (10, 0))
        db.set_value.assert_called_once_with("CN Complementary Item", "COMP", "result", "Saldo a favor documentado", update_modified=False)

    def test_credit_does_not_exceed_remaining_or_cross_company(self):
        for item, reason in ((self.document(name="COMP", amount_usd=11), "Excede saldo sin distribuir"),
                             (self.document(name="COMP", employer="OTHER"), "Empresa no coincide")):
            account, db = self.classify([item])
            self.assertEqual((account.justified_surplus_usd, account.unclassified_usd), (0, 10))
            self.assertEqual(db.set_value.call_args.args[3], reason)

    def test_multiple_credits_cannot_document_the_same_balance_twice(self):
        account, db = self.classify([self.document(name="C1", amount_usd=7), self.document(name="C2", amount_usd=7)])
        self.assertEqual((account.justified_surplus_usd, account.unclassified_usd), (7, 3))
        self.assertEqual(db.set_value.call_args.args[3], "Excede saldo sin distribuir")

    def test_migration_on_fresh_install_does_not_need_old_table(self):
        db = Mock(table_exists=Mock(return_value=False), exists=Mock(return_value=False))
        with patch.object(migration.frappe, "db", db), patch.object(migration, "_move_references"), patch.object(migration.frappe, "clear_cache"), patch.object(migration.frappe, "delete_doc") as delete:
            migration.execute()
        db.sql.assert_not_called()
        delete.assert_not_called()
