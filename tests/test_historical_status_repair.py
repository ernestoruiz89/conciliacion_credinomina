import copy
import json
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import historical_status_repair as repair
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from tests.test_closed_operative_reconciliation import _allocation


class HistoricalStatusRepairTests(unittest.TestCase):
    def setUp(self):
        self.row = frappe._dict(name="APP-1", parent="IMPORT-1", event_type="Aplicacion", effective=1,
            match_status="Conciliado", deposit_match_status="Sin deposito", deposit_match_reason=repair.OVERWRITTEN_REASON,
            historical_period="PER-1", collection_period="PER-1", application_allocation_detail="[]",
            currency="USD", amount=123.55, amount_usd=123.55, historical_remitted_usd=123.55, historical_balance_usd=0,
            historical_detail=json.dumps([{"referencia": "REF", "comprobante": "VOUCHER", "fecha": "2025-07-15", "importe_usd": 123.55}]))
        self.period = frappe._dict(name="PER-1", employer="EMP", reconciliation_mode="Historica", status="Cerrado")
        self.deposit = frappe._dict(name="DEP-1", employer="EMP", docstatus=1, deposit_reference="REF",
            deposit_voucher="VOUCHER", deposit_date="2025-07-15", allocation_detail=json.dumps([
                {"tipo": "Aplicacion historica", "periodo": "PER-1", "aplicacion_id": "APP-1", "importe_usd": 123.55}]))

    def verified(self):
        return repair.verified_status(self.row, self.period, "EMP", repair.deposit_evidence([self.deposit]).get(self.row.name))

    def test_operative_pass_preserves_historical_cash_status_including_partial(self):
        for status in ("Depósito conciliado", "Depósito parcial", "Sin deposito", "Conciliada: depósito + ajuste"):
            with self.subTest(status=status):
                self.row.deposit_match_status = status
                self.row.deposit_match_reason = "Historical cash evidence"
                before = copy.deepcopy(self.row)
                engine._rebuild_period_balances([], [self.row], [], _allocation(), {}, {}, [])
                self.assertEqual(self.row, before)

    def test_operative_unlinked_application_is_still_pending(self):
        self.row.historical_period = None
        self.row.deposit_match_status = "Depósito conciliado"
        engine._rebuild_period_balances([], [self.row], [], _allocation(), {}, {}, [])
        self.assertEqual(self.row.deposit_match_status, "Sin deposito")

    def test_restores_only_status_and_reason_when_both_sides_agree(self):
        changes = self.verified()
        self.assertEqual(set(changes), {"deposit_match_status", "deposit_match_reason"})
        self.assertEqual(changes["deposit_match_status"], "Depósito conciliado")

    def test_closed_period_and_zero_balance_alone_do_not_prove_payment(self):
        for changes in ({"docstatus": 0}, {"docstatus": 2}, {"employer": "OTHER"},
                        {"allocation_detail": "[]"}, {"allocation_detail": "invalid"}, {"deposit_reference": "OTHER"}):
            with self.subTest(changes=changes):
                original = copy.deepcopy(self.deposit)
                self.deposit.update(changes)
                self.assertIsNone(self.verified())
                self.deposit = original

    def test_mismatched_allocation_identity_period_or_amount_is_not_repaired(self):
        for changes in ({"aplicacion_id": "OTHER"}, {"periodo": "OTHER"}, {"importe_usd": 123.54},
                        {"importe_usd": 124}, {"importe_usd": "invalid"}):
            with self.subTest(changes=changes):
                original = self.deposit.allocation_detail
                entries = json.loads(original)
                entries[0].update(changes)
                self.deposit.allocation_detail = json.dumps(entries)
                self.assertIsNone(self.verified())
                self.deposit.allocation_detail = original

    def test_real_or_unverified_exceptions_are_preserved(self):
        for changes in ({"historical_balance_usd": 1}, {"historical_remitted_usd": 122.55},
                        {"historical_detail": "invalid"}, {"historical_detail": "[]"},
                        {"rounding_adjustment_usd": -0.01}, {"application_adjustment_usd": 1},
                        {"match_status": "Ambiguo"}, {"effective": 0}, {"deposit_match_reason": "Another issue"}):
            with self.subTest(changes=changes):
                original = copy.deepcopy(self.row)
                self.row.update(changes)
                self.assertIsNone(self.verified())
                self.row = original
        self.period.reconciliation_mode = "Operativa"
        self.assertIsNone(self.verified())

    def test_multiple_deposits_must_agree_on_each_saved_reference(self):
        other = copy.deepcopy(self.deposit)
        other.deposit_reference = "REF-2"
        other.allocation_detail = other.allocation_detail.replace("123.55", "23.55")
        self.deposit.allocation_detail = self.deposit.allocation_detail.replace("123.55", "100")
        details = json.loads(self.row.historical_detail)
        details[0]["importe_usd"] = 100
        details.append({**details[0], "referencia": "REF-2", "importe_usd": 23.55})
        self.row.historical_detail = json.dumps(details)
        evidence = repair.deposit_evidence([self.deposit, other])[self.row.name]
        self.assertIsNotNone(repair.verified_status(self.row, self.period, "EMP", evidence))

    def test_repair_dry_run_then_updates_header_without_saving_and_is_idempotent(self):
        doc = frappe._dict(doctype="CN Accounting Import", name="IMPORT-1", employer="EMP", docstatus=0,
            status="Importado con excepciones", rows=[self.row], exception_count=1, total_usd=123.55)
        doc.recalculate_reconciliation_summary = lambda: engine.CNAccountingImport.recalculate_reconciliation_summary(doc)
        doc.save = Mock(side_effect=AssertionError("Must not save"))
        def get_all(doctype, **kwargs):
            return {"CN Source Row": [frappe._dict(parent=doc.name)],
                "CN Reconciliation Period": [self.period], "CN Remittance Allocation": [self.deposit]}[doctype]
        with patch.object(repair.frappe, "get_all", side_effect=get_all), \
             patch.object(repair.frappe, "get_doc", side_effect=lambda *a: copy.deepcopy(doc)), \
             patch.object(repair.frappe, "db", Mock()) as db, \
             patch.object(repair.frappe, "clear_document_cache"):
            self.assertEqual(repair.repair_historical_statuses()["rows_repaired"], 1)
            db.set_value.assert_not_called()
        with patch.object(repair.frappe, "get_all", side_effect=get_all), \
             patch.object(repair.frappe, "get_doc", return_value=doc), \
             patch.object(repair.frappe, "db", Mock()) as db, \
             patch.object(repair.frappe, "clear_document_cache"):
            self.assertEqual(repair.repair_historical_statuses(False)["rows_repaired"], 1)
            self.assertEqual(db.set_value.call_count, 2)
            self.assertEqual((doc.status, doc.exception_count, doc.total_usd), ("Importado", 0, 123.55))
            db.reset_mock()
            self.assertEqual(repair.repair_historical_statuses(False)["rows_repaired"], 0)
            db.set_value.assert_not_called()
        doc.save.assert_not_called()
