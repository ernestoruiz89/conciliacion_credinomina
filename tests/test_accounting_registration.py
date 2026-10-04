import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation.accounting_registration import base_registration_status, exception_registration_status
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.follow_up_queue import build_follow_up
from credinomina_reconciliation.patches.v1_0 import clarify_accounting_registration as migration


class AccountingRegistrationTests(unittest.TestCase):
    def test_manual_voucher_is_not_evidence_even_when_financially_reconciled(self):
        item = dict(name="MANUAL", category="Cobranza administrativa", docstatus=1,
                    amount_usd=20, voucher="ABC")
        item["accounting_status"] = base_registration_status(item)
        self.assertEqual(item["accounting_status"], "Asiento informado")
        balance = financial_balance(item, [{"amount_usd": 20}])
        self.assertEqual(balance["financial_status"], "Conciliada")
        tasks = build_follow_up([item], {"MANUAL": balance}, [])
        self.assertEqual([t["kind"] for t in tasks], ["accounting_registration"])

    def test_imported_record_requires_original_ledger_evidence(self):
        item = dict(accounting_source_key="KEY", source_file="/private/files/ledger.csv",
                    source_file_hash="HASH", source_row=2, source_account="3004", source_voucher="ABC",
                    source_date="2026-03-01", source_currency="NIO", source_debit=36.62, source_credit=0)
        self.assertEqual(base_registration_status(item), "Importada del core")
        for field in ("source_file", "source_file_hash", "source_account", "source_date", "accounting_source_key", "source_row"):
            with self.subTest(field=field):
                self.assertEqual(base_registration_status({**item, field: None}), "Asiento informado")
        self.assertEqual(base_registration_status({**item, "source_credit": 1}), "Asiento informado")
        self.assertEqual(base_registration_status({**item, "source_currency": "EUR"}), "Asiento informado")

    def test_exception_keeps_informed_separate_from_verified(self):
        self.assertEqual(exception_registration_status(dict(status="En revision", core_voucher="ABC")), "Asiento informado")
        self.assertEqual(exception_registration_status(dict(status="Resuelta", core_voucher="ABC")), "Asiento informado")
        self.assertEqual(exception_registration_status(dict(status="Resuelta", core_voucher="ABC", core_evidence_key="KEY")), "Registrada")
        self.assertEqual(base_registration_status(dict(category="Diferencia por tolerancia")), "No requiere registro")

    def test_migration_is_idempotent_and_does_not_touch_financial_evidence(self):
        manual = frappe._dict(name="OLD", voucher="ABC", accounting_status="Registrada", amount_usd=20)
        def update(doctype, name, field, value, **kwargs):
            self.assertEqual((doctype, name, field), ("CN Complementary Item", "OLD", "accounting_status"))
            self.assertFalse(kwargs["update_modified"])
            manual[field] = value
        db = Mock(set_value=Mock(side_effect=update))
        with patch.object(migration.frappe, "get_all", side_effect=[[manual], [], [manual], []]), \
             patch.object(migration.frappe, "db", db), patch.object(migration.frappe, "clear_document_cache"):
            migration.execute()
            migration.execute()
        self.assertEqual(manual.accounting_status, "Asiento informado")
        self.assertEqual(manual.amount_usd, 20)
        db.set_value.assert_called_once()
