import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import complementary_exceptions as api


class ComplementaryExceptionTests(unittest.TestCase):
    def setUp(self):
        self.item = frappe._dict(name="MANUAL", doctype=api.ITEM, docstatus=1,
            category="Ajuste de conciliación", employer="REPSA", amount_usd=-0.01,
            amount=-0.01, currency="USD", period="CLOSED", description="Centavo pendiente",
            check_permission=Mock(), distribution_companies=[], compensations=[])
        self.ledger = frappe._dict(name="LEDGER", doctype=api.ITEM, docstatus=0,
            category="Por clasificar", review_action="Pendiente de revisión", employer="REPSA",
            source_voucher="00012", source_account="3004", accounting_source_key="KEY",
            source_currency="NIO", source_credit=0.37, source_debit=0, source_fx_rate=36.6243,
            source_file="/private/files/ledger.csv", source_row=2, source_date="2025-09-30",
            source_description="Registro centavo", check_permission=Mock(), save=Mock(), compensations=[])
        self.import_doc = frappe._dict(name="IMPORT", doctype=api.IMPORT, docstatus=0,
            employer="REPSA", status="Importado", source_file=self.ledger.source_file,
            manual_fx_rate=36.6243, check_permission=Mock())
        self.source = frappe._dict(self.ledger, name="ROW", doctype=api.SOURCE,
            parent="IMPORT", parentfield="rows", parenttype=api.IMPORT,
            event_type="Ajuste", voucher="00012", complementary_item="LEDGER")
        self.exception = frappe._dict(name="EXC", doctype=api.EXCEPTION, complementary_item="MANUAL",
            status="En revision", assigned_to="Administrator", commitment_date="2026-10-03",
            next_action=api.ACTION, check_permission=Mock(), follow_up_actions=[])
        self.docs = {(api.ITEM, "MANUAL"): self.item, (api.ITEM, "LEDGER"): self.ledger,
            (api.SOURCE, "ROW"): self.source, (api.IMPORT, "IMPORT"): self.import_doc,
            (api.EXCEPTION, "EXC"): self.exception}
        self.db = Mock()
        self.db.get_value.return_value = None
        targets = [
            patch.object(api, "_", side_effect=lambda text: text),
            patch.object(api.frappe, "throw", side_effect=lambda msg, *args, **kwargs: (_ for _ in ()).throw(frappe.ValidationError(msg))),
            patch.object(api.frappe, "get_doc", side_effect=lambda kind, name: self.docs[(kind, name)]),
            patch.object(api.frappe, "db", self.db),
            patch.object(api.frappe, "flags", frappe._dict(mute_messages=False)),
            patch.object(api.frappe, "session", frappe._dict(user="Administrator")),
            patch.object(api.frappe, "clear_document_cache"),
        ]
        for target in targets:
            target.start()
            self.addCleanup(target.stop)

    def evidence(self, kind=api.ITEM, name="LEDGER"):
        return api._evidence(kind, name, self.item, "00012")

    def test_original_nio_credit_converts_negative_cent_with_decimal(self):
        evidence = self.evidence()
        self.assertEqual(evidence["amount_usd"], -0.01)
        self.assertEqual(evidence["original_amount"], -0.37)
        self.assertEqual(len(evidence["fingerprint"]), 64)
        self.ledger.voucher = "MODIFIED"
        self.assertEqual(self.evidence()["voucher"], "00012")  # Never editable voucher.

    def test_positive_debit_and_usd_supported(self):
        self.item.amount_usd = 0.01
        self.ledger.source_currency = "USD"
        self.ledger.source_debit, self.ledger.source_credit = 0.01, 0
        self.assertEqual(self.evidence()["amount_usd"], 0.01)

    def test_wrong_sign_amount_company_or_voucher_rejected(self):
        for field, value in [("source_credit", 0.74), ("source_debit", 0.37),
                             ("employer", "OTHER"), ("source_voucher", "OTHER")]:
            original = self.ledger[field]
            self.ledger[field] = value
            with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
                self.evidence()
            self.ledger[field] = original

    def test_missing_ledger_evidence_rate_or_unknown_currency_rejected(self):
        for field, value in [("source_fx_rate", 0), ("source_currency", "EUR"),
                             ("source_file", ""), ("source_account", ""), ("accounting_source_key", "")]:
            original = self.ledger[field]
            self.ledger[field] = value
            with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
                self.evidence()
            self.ledger[field] = original

    def test_general_party_uses_only_explicit_authorized_companies(self):
        self.item.generic_distribution = 1
        self.item.distribution_companies = [frappe._dict(employer="OTHER")]
        self.ledger.employer = "OTHER"
        self.assertEqual(self.evidence()["employer"], "OTHER")
        self.ledger.employer = "NO IDENTIFICADA"
        with self.assertRaises(frappe.ValidationError):
            self.evidence()

    def test_client_and_credit_mismatch_do_not_auto_verify(self):
        self.item.loan_number, self.ledger.loan_number = "1-1", "2-1"
        with self.assertRaises(frappe.ValidationError):
            self.evidence()

    def test_evidence_with_financial_effects_cannot_be_reused(self):
        for field, value in [("docstatus", 1), ("related_application", "A"),
                             ("review_action", "Partida de depósito")]:
            original = self.ledger.get(field)
            self.ledger[field] = value
            with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
                self.evidence()
            self.ledger[field] = original

    def test_import_child_requires_visible_finished_parent_and_adjustment(self):
        self.assertEqual(self.evidence(api.SOURCE, "ROW")["import"], "IMPORT")
        self.import_doc.status = "Pendiente"
        with self.assertRaises(frappe.ValidationError):
            self.evidence(api.SOURCE, "ROW")
        self.import_doc.status = "Importado"
        for event in ("Aplicacion", "Deposito"):
            self.source.event_type = event
            with self.assertRaises(frappe.ValidationError):
                self.evidence(api.SOURCE, "ROW")
        self.source.event_type = "Ajuste"
        self.import_doc.check_permission.side_effect = frappe.PermissionError
        with self.assertRaises(frappe.PermissionError):
            self.evidence(api.SOURCE, "ROW")

    def test_followup_is_periodless_but_records_closed_origin(self):
        api.validate_exception(self.exception)
        self.assertEqual(self.exception.origin_period, "CLOSED")
        self.assertFalse(self.exception.period)
        self.assertEqual(self.exception.amount_usd, -0.01)
        self.db.set_value.assert_not_called()

    def test_responsible_date_next_action_required_for_accounting_followup(self):
        for field in ("assigned_to", "commitment_date", "next_action"):
            original = self.exception[field]
            self.exception[field] = ""
            with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
                api.validate_exception(self.exception)
            self.exception[field] = original

    def test_voucher_alone_cannot_resolve_or_discard(self):
        self.exception.core_voucher = "00012"
        for status in ("Resuelta", "Descartada"):
            self.exception.status = status
            with self.assertRaises(frappe.ValidationError):
                api.validate_exception(self.exception)

    def test_origin_and_verification_cannot_be_forged_or_unlinked(self):
        previous = frappe._dict(self.exception)
        self.exception.complementary_item = ""
        with self.assertRaises(frappe.ValidationError):
            api.validate_exception(self.exception, previous)
        self.exception.complementary_item = "MANUAL"
        self.exception.core_evidence_key = "FORGED"
        with self.assertRaises(frappe.ValidationError):
            api.validate_exception(self.exception, previous)
        self.exception.core_evidence_key = ""
        self.exception.period = "OPEN"
        with self.assertRaises(frappe.ValidationError):
            api.validate_exception(self.exception, previous)

    def test_verified_fingerprint_detects_replaced_evidence(self):
        evidence = self.evidence()
        self.exception.update(core_voucher="00012", core_evidence_type=api.ITEM,
            core_evidence_name="LEDGER", core_evidence_key="KEY", core_evidence_fingerprint=evidence["fingerprint"])
        previous = frappe._dict(self.exception)
        api.validate_exception(self.exception, previous)
        self.ledger.source_description = "Changed"
        with self.assertRaises(frappe.ValidationError):
            api.validate_exception(self.exception, previous)

    def test_candidate_search_deduplicates_mirrors_not_physical_ledger_lines(self):
        with patch.object(api.frappe, "get_list", side_effect=[["IMPORT"], ["LEDGER"]]), \
             patch.object(api.frappe, "get_all", return_value=["ROW"]):
            candidates = api.get_registration_candidates("EXC", "00012")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["type"], api.SOURCE)
        self.assertFalse(api.frappe.flags.mute_messages)

    def test_candidate_search_rejects_other_use_and_restores_messages(self):
        self.db.get_value.return_value = "OTHER-EXCEPTION"
        with patch.object(api.frappe, "get_list", side_effect=[[], ["LEDGER"]]):
            self.assertEqual(api.get_registration_candidates("EXC", "00012"), [])
        self.ledger.source_credit = 30
        with patch.object(api.frappe, "get_list", side_effect=[[], ["LEDGER"]]):
            self.assertEqual(api.get_registration_candidates("EXC", "00012"), [])
        self.assertFalse(api.frappe.flags.mute_messages)

    def test_create_is_idempotent_and_requires_permission(self):
        with patch.object(api, "get_item_exception", return_value="EXISTING"):
            self.assertEqual(api.create_item_exception("MANUAL", "Administrator", "2026-10-03"), "EXISTING")
        with patch.object(api, "get_item_exception", return_value=None), \
             patch.object(api.frappe, "has_permission", return_value=False):
            with self.assertRaises(frappe.ValidationError):
                api.create_item_exception("MANUAL", "Administrator", "2026-10-03")

    def test_registration_status_preserved_by_tolerance_recalculation(self):
        self.item.accounting_exception = "EXC"
        for state, key, expected in [("En revision", "", "Pendiente de registro"),
                                     ("Resuelta", "KEY", "Registrada"),
                                     ("Resuelta", "", "Pendiente de registro")]:
            self.db.get_value.return_value = frappe._dict(complementary_item="MANUAL", status=state, core_evidence_key=key)
            api.apply_registration_status(self.item)
            self.assertEqual(self.item.accounting_status, expected)

    def test_resolution_rechecks_used_evidence_permission_and_text(self):
        with self.assertRaises(frappe.ValidationError):
            api.verify_and_resolve("EXC", "00012", api.ITEM, "LEDGER", "")
        self.db.get_value.return_value = "OTHER-EXCEPTION"
        with self.assertRaises(frappe.ValidationError):
            api.verify_and_resolve("EXC", "00012", api.ITEM, "LEDGER", "Registered")
        self.exception.check_permission.side_effect = frappe.PermissionError
        with self.assertRaises(frappe.PermissionError):
            api.verify_and_resolve("EXC", "00012", api.ITEM, "LEDGER", "Registered")

    def test_bookkeeping_links_do_not_permit_changing_verified_item_or_evidence_use(self):
        self.item.accounting_exception = "EXC"
        previous = frappe._dict(self.item)
        self.db.get_value.return_value = "KEY"
        self.item.amount = -0.02
        with self.assertRaises(frappe.ValidationError):
            api.guard_item_link(self.item, previous)
        self.item.amount = -0.01
        self.item.accounting_exception = ""
        with self.assertRaises(frappe.ValidationError):
            api.guard_item_link(self.item, previous)
        self.ledger.registration_exception = "EXC"
        self.db.get_value.return_value = frappe._dict(name="EXC", complementary_item="MANUAL")
        self.ledger.review_action = "No conciliatoria"
        api.guard_registered_evidence(self.ledger)
        self.ledger.docstatus = 1
        with self.assertRaises(frappe.ValidationError):
            api.guard_registered_evidence(self.ledger)

    def test_schema_unique_origin_proof_and_no_copy(self):
        base = Path(__file__).resolve().parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype"
        schema = json.loads((base / "cn_reconciliation_exception/cn_reconciliation_exception.json").read_text())
        fields = {field["fieldname"]: field for field in schema["fields"]}
        for name in ("complementary_item", "core_evidence_key"):
            self.assertTrue(fields[name]["unique"])
            self.assertTrue(fields[name]["no_copy"])
        for name in api.PROOF_FIELDS:
            self.assertTrue(fields[name]["read_only"])
        self.assertTrue(fields["origin_period"]["read_only"])

    def test_verified_import_protects_original_rows_not_derived_reconciliation(self):
        previous = frappe._dict(rows=[frappe._dict(self.source)], employer="REPSA", source_file="file.csv", currency="USD")
        current = frappe._dict(rows=[frappe._dict(self.source)], employer="REPSA", source_file="file.csv", currency="USD")
        with patch.object(api.frappe, "get_all", return_value=["ROW"]):
            current.rows[0].match_status = "Ignorado"
            api.guard_verified_import(current, previous, ("source_credit", "voucher"))
            current.rows[0].source_credit = 0.38
            with self.assertRaises(frappe.ValidationError):
                api.guard_verified_import(current, previous, ("source_credit", "voucher"))
            current.rows = []
            with self.assertRaises(frappe.ValidationError):
                api.guard_verified_import(current, previous, ("source_credit", "voucher"))


if __name__ == "__main__":
    unittest.main()
