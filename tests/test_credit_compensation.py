"""Core evidence offsets must never release deposits or invent client refunds."""
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import complementary_compensation as api
from credinomina_reconciliation import complementary_exceptions
from credinomina_reconciliation.client_credit import FINANCIAL_FIELDS, MANAGED_FIELDS
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.accounting_control import build_rows


def credit_item(**changes):
    values = dict(name="CREDIT", doctype=api.DOCTYPE, docstatus=1, category="Saldo a favor del cliente",
        review_action="Saldo a favor del cliente", review_status="Saldo a favor del cliente identificado",
        amount=19.25, amount_usd=19.25, currency="USD", fx_rate=36.6243,
        posting_date="2025-08-07", employer="MULTIPERFILES", registered_deposit="DEP",
        result="Saldo a favor documentado", client_name="DELVIN FRANCISCO GUTIERREZ BOBADILLA",
        client_number="5470", credit_client="5470", loan_number="109586-1", credit_detail_row="ROW",
        credit_pending_usd=19.25, credit_resolved_usd=0, credit_management_status="Pendiente", credit_history="[]",
        flags=frappe._dict(), compensations=[], check_permission=Mock())
    return frappe._dict(values | changes)


def ledger_item(**changes):
    values = dict(name="LEDGER", doctype=api.DOCTYPE, docstatus=0, category="Por clasificar",
        review_action="Pendiente de revisión", employer="MULTIPERFILES", amount=19.25, amount_usd=19.25,
        currency="USD", posting_date="2025-11-21", accounting_source_key="KEY", source_account="160209013004",
        source_file="/private/files/core.xlsx", source_file_hash="HASH", source_row=20936,
        source_date="2025-11-21", source_voucher="00101495", voucher="00101495", voucher_line="KEY",
        source_currency="NIO", source_fx_rate=36.6243, source_debit=704.89, source_credit=0,
        accounting_classification="Movimiento interno", source_client_name="DELVIN FRANCISCO GUTIERREZ BOBADILLA",
        description="Traslado a la cuenta 3001", flags=frappe._dict(), compensations=[], check_permission=Mock())
    return frappe._dict(values | changes)


class CreditCompensationTests(unittest.TestCase):
    def setUp(self):
        self.credit, self.ledger = credit_item(), ledger_item()
        self.docs = {doc.name: doc for doc in (self.credit, self.ledger)}
        self.db = Mock(exists=Mock(return_value=False), get_value=Mock(return_value=None))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(frappe, "db", self.db))
        self.stack.enter_context(patch.object(frappe, "get_doc", side_effect=lambda dt, name, **kw: self.docs[name]))
        self.stack.enter_context(patch.object(frappe, "throw", side_effect=lambda message, *a, **kw: (_ for _ in ()).throw(ValueError(message))))
        self.stack.enter_context(patch.object(frappe, "session", frappe._dict(user="Supervisor")))
        self.stack.enter_context(patch.object(api, "nowdate", return_value="2026-10-05"))
        self.stack.enter_context(patch.object(api, "now_datetime", return_value="2026-10-05 20:00:00"))
        for doc in self.docs.values():
            doc.append = lambda field, values, doc=doc: doc[field].append(frappe._dict(values))
            doc.save = Mock(side_effect=lambda doc=doc: api.update_totals(doc))
            def submit(doc=doc):
                doc.docstatus = 1
                api.update_totals(doc)
            doc.submit = Mock(side_effect=submit)

    def confirm(self, amount=19.25, key="a" * 32):
        return api.confirm_compensation("CREDIT", "LEDGER", amount, "2025-11-21", "Traslado a 3001", key)

    def test_given_case_links_both_sides_without_changing_credit_or_refund(self):
        preserved = {field: self.credit.get(field) for field in (*FINANCIAL_FIELDS, *MANAGED_FIELDS, "result")}
        evidence = {key: value for key, value in self.ledger.items() if key.startswith("source_")}
        preview = api.preview_compensation("LEDGER", "CREDIT")
        self.assertEqual(preview["suggested_usd"], 19.25)
        self.assertTrue(preview["right"]["is_credit"])
        self.assertEqual(preview["right"]["management_pending_usd"], 19.25)
        self.confirm()
        self.confirm()  # Same request cannot consume the amounts twice.
        self.assertEqual({field: self.credit.get(field) for field in preserved}, preserved)
        self.assertEqual({field: self.ledger.get(field) for field in evidence}, evidence)
        self.assertEqual(self.ledger.category, api.CATEGORY)
        for doc, counterpart in ((self.credit, self.ledger), (self.ledger, self.credit)):
            self.assertEqual(doc.compensation_pending_usd, 0)
            self.assertEqual(doc.compensated_usd, 19.25)
            self.assertEqual(len(doc.compensations), 1)
            self.assertEqual(doc.compensations[0].counterpart, counterpart.name)
            self.assertNotIn("compensation_write_token", doc.flags)
            doc.check_permission.assert_any_call("submit")
        self.credit.submit.assert_not_called()
        self.ledger.submit.assert_called_once()
        self.credit.save.assert_called_once()
        self.assertEqual(financial_balance(self.ledger)["pending_usd"], 0)
        self.assertEqual(financial_balance(self.credit)["management_pending_usd"], 19.25)
        rows, duplicates = build_rows([], {}, [self.credit, self.ledger])
        self.assertEqual((len(rows), duplicates), (1, 0))
        self.assertEqual((rows[0]["debit_nio"], rows[0]["credit_nio"], rows[0]["debit_usd"]), (704.89, 0, 19.25))
        self.assertEqual(rows[0]["state"], "Compensada totalmente")
        self.db.set_value.assert_not_called()  # No deposit or management writes.

    def test_already_refunded_credit_can_link_evidence_without_refunding_twice(self):
        self.credit.credit_resolved_usd = 19.25
        self.credit.credit_pending_usd = 0
        self.credit.credit_management_status = "Resuelto"
        self.credit.credit_history = '[{"tratamiento":"Devolución","importe_usd":19.25}]'
        history = self.credit.credit_history
        self.confirm()
        self.assertEqual(self.credit.credit_history, history)
        self.assertEqual(self.credit.credit_pending_usd, 0)
        self.assertEqual(self.credit.credit_resolved_usd, 19.25)

    def test_partial_capacity_overuse_and_paired_reversal(self):
        self.confirm(10)
        self.assertEqual(api.balance(self.credit)["pending_usd"], 9.25)
        with self.assertRaises(ValueError):
            self.confirm(10, "b" * 32)
        self.confirm(9.25, "b" * 32)
        args = ("CREDIT", "a" * 32, "2025-11-22", "Corregir vínculo", "c" * 32)
        api.reverse_compensation(*args)
        api.reverse_compensation(*args)
        for doc in self.docs.values():
            self.assertEqual(api.balance(doc, "2025-11-21")["pending_usd"], 0)
            self.assertEqual(api.balance(doc)["pending_usd"], 10)
            self.assertEqual(len(doc.compensations), 3)
        self.assertEqual(self.credit.credit_pending_usd, 19.25)
        self.assertEqual(self.credit.category, "Saldo a favor del cliente")
        with self.assertRaises(ValueError):
            api.guard_delete(self.credit)

    def test_conflicting_identity_or_untrusted_evidence_is_rejected(self):
        changes = [dict(employer="OTHER"), dict(client_number="999"), dict(loan_number="999-1"),
            dict(source_client_name="OTHER PERSON"), dict(source_debit=0, source_credit=704.89),
            dict(amount_usd=20), dict(source_fx_rate=0), dict(source_file_hash=""),
            dict(accounting_source_key=""), dict(accounting_classification="Aplicación de pago"),
            dict(registered_deposit="DEP"), dict(related_application="APP"), dict(generic_distribution=1),
            dict(registration_exception="EXC")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                api._validate_pair(self.credit, ledger_item(**change))
        with self.assertRaises(ValueError):
            api._validate_pair(self.credit, credit_item(name="OTHER"))
        for change in (dict(docstatus=0), dict(docstatus=2), dict(result="Pendiente"), dict(registered_deposit="")):
            with self.subTest(change=change), self.assertRaises(ValueError):
                api._validate_pair(credit_item(**change), self.ledger)

    def test_company_credit_and_matching_ids_without_source_name_are_supported(self):
        api._validate_pair(credit_item(category="Saldo a favor de la empresa"), self.ledger)
        api._validate_pair(self.credit, ledger_item(client_number="5470", source_client_name=""))
        api._validate_pair(self.credit, ledger_item(loan_number="109586", source_client_name=""))
        api._validate_pair(self.credit, ledger_item(source_client_name="GUTIERREZ BOBADILLA DELVIN FRANCISCO"))

    def test_existing_verified_evidence_and_reserved_core_cannot_be_reused(self):
        self.credit.accounting_exception = "EXC"
        self.db.get_value.return_value = "CORE-KEY"
        with self.assertRaises(ValueError):
            self.confirm()
        self.credit.accounting_exception = ""
        self.db.exists.return_value = True
        with self.assertRaises(ValueError):
            self.confirm()
        self.credit.save.assert_not_called()
        self.ledger.submit.assert_not_called()

    def test_offset_blocks_a_second_exception_verification_but_not_follow_up(self):
        self.confirm()
        with self.assertRaisesRegex(ValueError, "ya tiene evidencia"):
            complementary_exceptions._evidence(api.DOCTYPE, "OTHER", self.credit, "00101495")

    def test_credit_compensation_sections_are_visible(self):
        source = Path(__file__).parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_complementary_item/cn_complementary_item.json"
        fields = {row["fieldname"]: row for row in json.loads(source.read_text(encoding="utf-8"))["fields"]}
        for field in ("compensation_section", "compensation_history_section"):
            self.assertIn("doc.compensations", fields[field]["depends_on"])
