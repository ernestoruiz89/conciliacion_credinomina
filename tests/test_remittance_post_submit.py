"""Exercise Frappe's post-submit field guard after the remittance hook."""

import copy
import json
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.model.base_document import BaseDocument

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import CNRemittanceAllocation
from credinomina_reconciliation.rounding import money


class Snapshot(dict):
    __getattr__ = dict.get

    def as_dict(self):
        return dict(self)

    def get_value(self, key):
        return self.get(key)

    @property
    def result(self):
        return self.get("result")

    @result.setter
    def result(self, value):
        self["result"] = value


class RemittancePostSubmitTests(unittest.TestCase):
    def setUp(self):
        for patcher in (
            patch("credinomina_reconciliation.client_credit.load_credits", return_value=[]),
            patch("credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation.now_datetime", return_value="2026-09-30 00:00:00"),
            patch("credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation._", side_effect=lambda text: text),
            patch.dict(frappe.__dict__, {"session": SimpleNamespace(user="operator@example.test")}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        path = Path(__file__).resolve().parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.json"
        self.fields = {field["fieldname"]: frappe._dict(field)
                       for field in json.loads(path.read_text(encoding="utf-8"))["fields"]}
        self.previous = Snapshot(
            doctype="CN Remittance Allocation", name="REM-TEST", docstatus=1,
            result="Revisar detalle", deposit_amount=100, fx_rate=36.5, notes="",
            deposit_reference="REF", deposit_voucher="",
            targets=[{"historical_application": "APP-1", "amount_usd": 40}],
        )

    def current(self, **changes):
        doc = Snapshot(copy.deepcopy(dict(self.previous)) | changes)
        doc.meta = SimpleNamespace(get_field=self.fields.get)
        doc._validate_deposit = Mock()
        doc.flags = frappe._dict()
        if doc.get("detail_rows"):
            doc["detail_rows"] = [frappe._dict(row) for row in doc["detail_rows"]]
        doc._invalidate_changed_detail_credits = MethodType(
            CNRemittanceAllocation._invalidate_changed_detail_credits, doc,
        )
        doc.get_doc_before_save = lambda: self.previous
        doc._reconciliation_inputs_changed = MethodType(
            CNRemittanceAllocation._reconciliation_inputs_changed, doc,
        )
        return doc

    def check_frappe_guard(self, doc):
        with (
            patch("frappe.get_doc", return_value=self.previous),
            patch("frappe.throw", side_effect=frappe.UpdateAfterSubmitError),
            patch("frappe.model.base_document._", side_effect=lambda value, **_kw: value),
        ):
            BaseDocument._validate_update_after_submit(doc)

    def test_add_edit_remove_targets_marks_pending_and_can_be_saved(self):
        for targets in (
            self.previous.targets + [{"historical_application": "APP-2", "amount_usd": 60}],
            [{"historical_application": "APP-1", "amount_usd": 50}],
            [{"historical_application": "APP-1", "amount_usd": 40, "detail_row": "ROW-1"}],
            [],
        ):
            with self.subTest(targets=targets):
                doc = self.current(targets=targets)
                CNRemittanceAllocation.before_update_after_submit(doc)
                self.assertEqual(doc.result, "Pendiente")
                self.check_frappe_guard(doc)
                doc._validate_deposit.assert_called_once_with()

    def test_rate_change_can_also_mark_pending(self):
        doc = self.current(fx_rate=36.6)
        CNRemittanceAllocation.before_update_after_submit(doc)
        self.assertEqual(doc.result, "Pendiente")
        self.check_frappe_guard(doc)

    def test_manual_period_opt_in_can_be_changed_after_submit_and_marks_pending(self):
        for old, new in ((0, 1), (1, 0)):
            self.previous["allow_manual_other_periods"] = old
            doc = self.current(allow_manual_other_periods=new)
            CNRemittanceAllocation.before_update_after_submit(doc)
            self.assertEqual(doc.result, "Pendiente")
            self.check_frappe_guard(doc)

    def test_detail_credit_selection_marks_pending_after_confirmation(self):
        self.previous["detail_rows"] = [{"name": "ROW-1", "client": "3538", "loan_number": ""}]
        doc = self.current(detail_rows=[{"name": "ROW-1", "client": "3538", "client_number": "3538", "loan_number": "108331-1"}])
        CNRemittanceAllocation.before_update_after_submit(doc)
        self.assertEqual(doc.result, "Pendiente")
        self.check_frappe_guard(doc)

    def test_notes_only_preserve_the_existing_result(self):
        doc = self.current(notes="Soporte recibido")
        CNRemittanceAllocation.before_update_after_submit(doc)
        self.assertEqual(doc.result, "Revisar detalle")
        self.check_frappe_guard(doc)

    def test_target_decimal_rounding_does_not_invalidate_an_unchanged_result(self):
        self.previous["result"] = "Conciliado"
        self.previous["targets"][0]["amount_usd"] = 1000.0
        doc = self.current(notes="Soporte recibido")
        doc._validate_deposit.side_effect = lambda: doc.targets[0].update(amount_usd=money(1000.0))
        CNRemittanceAllocation.before_update_after_submit(doc)
        self.assertEqual(doc.result, "Conciliado")
        self.check_frappe_guard(doc)

    def test_amount_and_rate_representations_do_not_mark_pending(self):
        self.previous["result"] = "Conciliado"
        doc = self.current(deposit_amount=money(100), fx_rate="36.5000")
        CNRemittanceAllocation.before_update_after_submit(doc)
        self.assertEqual(doc.result, "Conciliado")

    def test_late_detail_preserves_server_recorded_balances_when_form_sends_zeroes(self):
        self.previous.update(
            allocated_usd=0, unallocated_usd=1445.11, justified_surplus_usd=0,
            unclassified_usd=1445.11, allocation_detail="[]",
            inherited_exception_comment="Excepción documentada",
        )
        doc = self.current(
            detail_file="/private/files/detalle.csv",
            allocated_usd=0, unallocated_usd=0, justified_surplus_usd=0,
            unclassified_usd=0, allocation_detail="", inherited_exception_comment="",
        )
        CNRemittanceAllocation.before_update_after_submit(doc)
        self.assertEqual(doc.result, "Pendiente")
        self.check_frappe_guard(doc)
        for field in (
            "allocated_usd", "unallocated_usd", "justified_surplus_usd",
            "unclassified_usd", "allocation_detail", "inherited_exception_comment",
        ):
            self.assertEqual(doc.get(field), self.previous.get(field), field)

    def test_new_detail_rows_keep_partial_distribution_until_explicit_reconciliation(self):
        self.previous.update(
            allocated_usd=1000, unallocated_usd=445.11, justified_surplus_usd=100,
            unclassified_usd=345.11,
            allocation_detail='[{"aplicacion_id":"APP-1","importe_usd":1000}]',
        )
        doc = self.current(
            detail_rows=[{"name": "ROW-NEW", "client_name": "Cliente", "deducted_usd": 1445.11}],
            allocated_usd=1445.11, unallocated_usd=0, justified_surplus_usd=0,
            unclassified_usd=0, allocation_detail="[]",
        )
        CNRemittanceAllocation.before_update_after_submit(doc)
        self.assertEqual(doc.result, "Pendiente")
        self.check_frappe_guard(doc)
        self.assertEqual(doc.allocated_usd, 1000)
        self.assertEqual(doc.unallocated_usd, 445.11)
        self.assertEqual(doc.allocation_detail, self.previous.allocation_detail)
        self.assertEqual(doc.targets, self.previous.targets)

    def test_plain_save_cannot_mark_a_deposit_reconciled(self):
        doc = self.current(result="Conciliado", notes="Observación")
        CNRemittanceAllocation.before_update_after_submit(doc)
        self.assertEqual(doc.result, "Revisar detalle")
        self.check_frappe_guard(doc)

    def test_correcting_confirmed_credit_clears_stale_matches_but_keeps_targets(self):
        self.previous["result"] = "Conciliado"
        self.previous["detail_rows"] = [{
            "name": "ROW-1", "loan_number": "108331-1", "client": "3538",
            "loan_selection_snapshot": "APR", "loan_selection_note": "Selección anterior",
            "match_status": "Conciliada", "matched_targets": '[{"claim_id":"H:APP-1"}]',
            "matched_targets_summary": "Aplicación anterior", "amount_usd": 40,
        }]
        for credit in ("109136-1", ""):
            with self.subTest(credit=credit):
                doc = self.current(detail_rows=[self.previous.detail_rows[0] | {"loan_number": credit}])
                CNRemittanceAllocation.before_update_after_submit(doc)
                row = doc.detail_rows[0]
                self.assertEqual(row.match_status, "Pendiente")
                self.assertEqual(row.matched_targets, "[]")
                self.assertIsNone(row.loan_selection_snapshot)
                self.assertIn("Edición manual", row.loan_selection_note)
                self.assertEqual(doc.result, "Pendiente")
                self.assertEqual(row.amount_usd, 40)
                self.assertEqual(doc.targets, self.previous.targets)
                self.check_frappe_guard(doc)

    def test_portfolio_selector_keeps_its_validated_audit(self):
        self.previous["detail_rows"] = [{"name": "ROW-1", "loan_number": "OLD"}]
        doc = self.current(detail_rows=[{"name": "ROW-1", "loan_number": "NEW",
                                       "loan_selection_snapshot": "APR", "loan_selection_note": "Selección validada"}])
        doc.flags.portfolio_selected_detail = "ROW-1"
        CNRemittanceAllocation.before_update_after_submit(doc)
        self.assertEqual(doc.detail_rows[0].loan_selection_snapshot, "APR")
        self.assertEqual(doc.detail_rows[0].loan_selection_note, "Selección validada")

    def test_unchanged_credit_preserves_reconciled_row(self):
        self.previous["result"] = "Conciliado"
        self.previous["detail_rows"] = [{"name": "ROW-1", "loan_number": "108331-1",
                                       "match_status": "Conciliada", "matched_targets": "[1]"}]
        doc = self.current(notes="Observación")
        CNRemittanceAllocation.before_update_after_submit(doc)
        self.assertEqual(doc.result, "Conciliado")
        self.assertEqual(doc.detail_rows[0].matched_targets, "[1]")

    def test_result_remains_read_only_and_deposit_amount_stays_locked(self):
        self.assertTrue(self.fields["result"].read_only)
        with self.assertRaises(frappe.UpdateAfterSubmitError):
            self.check_frappe_guard(self.current(deposit_amount=200))
