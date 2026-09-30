"""A control cut preserves an auditable view without settling the period."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import (
    cn_reconciliation_period as period_module,
)


class PeriodControlCutTests(unittest.TestCase):
    def _period(self):
        return SimpleNamespace(
            name="PER-1", status="Detalle empresa cargado",
            reconciliation_mode="Operativa", collection_rows=[frappe._dict(
                deduction_status="Deduccion parcial", application_status="Depósito parcial",
                expected_usd=50, deducted_usd=30, applied_usd=30, remitted_usd=20,
            )],
            expected_usd=50, deducted_usd=30, applied_usd=30, remitted_usd=20,
            notes="", flags=frappe._dict(), check_permission=Mock(),
            recalculate_totals=Mock(), save=Mock(),
        )

    def test_cut_captures_receivables_and_keeps_period_open(self):
        period = self._period()
        db = SimpleNamespace(count=lambda *_: 1)
        with patch.object(period_module.frappe, "get_doc", return_value=period), \
             patch.object(period_module.frappe, "db", db), \
             patch.object(period_module.frappe, "session", SimpleNamespace(user="auditor")), \
             patch.object(period_module, "_reconcile_if_sources", return_value=None), \
             patch.object(period_module, "_pending_remittance_details_for_period", return_value=["R-1"]), \
             patch.object(period_module, "now_datetime", return_value=datetime(2026, 9, 28, 15, 20)):
            result = period_module.record_control_cut(
                period.name, "Gestionar saldo no deducido con empresa"
            )

        self.assertEqual(period.status, "Detalle empresa cargado")
        self.assertEqual(result["status"], period.status)
        self.assertIn("CxC empleados confirmada US$ 20.00", period.control_cut_summary)
        self.assertIn("deducido sin depósito asignado US$ 10.00", period.control_cut_summary)
        self.assertIn("excepciones abiertas 1", period.control_cut_summary)
        self.assertIn("detalles de depósito pendientes 1", period.control_cut_summary)
        self.assertIn("El período permanece abierto", period.notes)
        period.save.assert_called_once()

    def test_cut_uses_same_fx_and_rounding_balance_as_aging(self):
        period = self._period()
        period.collection_rows[0].fx_variance_usd = 2
        period.collection_rows[0].rounding_adjustment_usd = -0.01
        summary = period_module._control_cut_summary(period, 0, 0)
        self.assertIn("deducido sin depósito asignado US$ 7.99", summary)

    def test_history_has_no_invented_employee_receivable(self):
        period = self._period()
        period.reconciliation_mode = "Historica"
        period.collection_rows = []
        summary = period_module._control_cut_summary(period, 0, 1)
        self.assertIn("aplicado US$ 30.00", summary)
        self.assertNotIn("CxC empleados", summary)

    def test_partial_deduction_is_identified_not_fully_reconciled(self):
        rows = [frappe._dict(deduction_status="Deduccion parcial")]
        self.assertEqual(
            period_module._deduction_stage_status(rows, 0, 0),
            "Detalle empresa cargado",
        )
        rows[0].deduction_status = "Deduccion total"
        self.assertEqual(
            period_module._deduction_stage_status(rows, 0, 0),
            "Deduccion conciliada",
        )

    def test_detail_import_key_changes_after_manual_identity_correction(self):
        row = frappe._dict(
            name="ROW-1", row_key="stable-row", client="CLIENT-1",
            client_number="C-1", employee_number="E-1", client_name="Ana Perez",
            national_id="ID-1", loan_number="LOAN-1", installment_number="1",
            expected_usd=50, expected_nio=1800,
        )
        period = SimpleNamespace(
            deduction_evidence_date="2026-09-30",
            collection_import_sha256="collection-sha", collection_rows=[row],
        )
        before = period_module._employer_response_import_key(period, b"detail")
        row.client_name = "Ana P. Perez"
        self.assertNotEqual(
            before, period_module._employer_response_import_key(period, b"detail")
        )

    def test_detail_import_key_changes_when_linked_client_alias_changes(self):
        row = frappe._dict(
            name="ROW-1", row_key="stable-row", client="CLIENT-1",
            client_number="C-1", employee_number="E-1", client_name="Ana Perez",
            national_id="ID-1", loan_number="LOAN-1", installment_number="1",
            expected_usd=50, expected_nio=1800,
        )
        period = SimpleNamespace(
            employer="EMP-1", deduction_evidence_date="2026-09-30",
            collection_import_sha256="collection-sha", collection_rows=[row],
        )
        client = {
            "name": "CLIENT-1", "employer": "EMP-1", "client_name": "Ana Perez",
            "client_number": "C-1", "employee_number": "E-1",
            "national_id": "ID-1", "client_aliases": [],
        }
        before = period_module._employer_response_import_key(period, b"detail", [client])
        client["client_aliases"].append("Pérez Ana")
        self.assertNotEqual(
            before, period_module._employer_response_import_key(period, b"detail", [client])
        )

    def test_definitive_close_rejects_unsettled_row(self):
        period = self._period()
        period.recalculate_totals = Mock()
        period.collection_rows[0].application_status = "Aplicado y remitido"
        period.collection_rows[0].remitted_usd = 30
        with patch.object(period_module.frappe, "get_doc", return_value=period), \
             patch.object(period_module.frappe, "db", SimpleNamespace(count=lambda *_: 0)), \
             patch.object(period_module.frappe, "get_all", return_value=[]), \
             patch.object(period_module.frappe, "throw", side_effect=ValueError) as reject, \
             patch.object(period_module, "_reconcile_if_sources", return_value=None), \
             patch.object(period_module, "_pending_remittance_details_for_period", return_value=[]), \
             patch.object(period_module, "_has_operative_application", return_value=True), \
             patch.object(period_module, "_pending_registered_targets", return_value=False):
            with self.assertRaises(ValueError):
                period_module.close_period(period.name)
        self.assertIn("saldos a empleados", reject.call_args.args[0].lower())

    def test_empty_operative_period_cannot_close(self):
        period = SimpleNamespace(
            name="PER-EMPTY", status="Borrador", reconciliation_mode="Operativa",
            collection_rows=[], check_permission=Mock(),
        )
        with patch.object(period_module.frappe, "get_doc", return_value=period), \
             patch.object(period_module.frappe, "throw", side_effect=ValueError) as reject, \
             patch.object(period_module, "_reconcile_if_sources", return_value=None):
            with self.assertRaises(ValueError):
                period_module.close_period(period.name)
        self.assertIn("Cargue la cobranza", reject.call_args.args[0])

    def test_historical_status_alone_cannot_close_without_applications(self):
        period = SimpleNamespace(
            name="HIST-EMPTY", status="Historico conciliado",
            reconciliation_mode="Historica", check_permission=Mock(),
        )
        with patch.object(period_module.frappe, "get_doc", return_value=period), \
             patch.object(period_module.frappe, "db", SimpleNamespace(count=lambda *_: 0)), \
             patch.object(period_module.frappe, "get_all", return_value=[]), \
             patch.object(period_module, "_reconcile_if_sources", return_value=None), \
             patch.object(period_module, "_pending_remittance_details_for_period", return_value=[]), \
             patch.object(period_module.frappe, "throw", side_effect=ValueError) as reject:
            with self.assertRaises(ValueError):
                period_module.close_period(period.name)
        self.assertIn("sin aplicaciones efectivas", reject.call_args.args[0])

    def test_grouped_application_can_close_second_quincena(self):
        period = SimpleNamespace(
            name="Q2", collection_rows=[SimpleNamespace(name="COL-Q2")],
        )
        db = SimpleNamespace(exists=lambda *_: None)
        sources = [frappe._dict(application_allocation_detail=(
            '[{"period":"Q1","collection_row_id":"COL-Q1","amount_usd":20},'
            '{"period":"Q2","collection_row_id":"COL-Q2","amount_usd":20}]'
        ))]
        with patch.object(period_module.frappe, "db", db), \
             patch.object(period_module.frappe, "get_all", return_value=sources):
            self.assertTrue(period_module._has_operative_application(period))

    def test_problematic_detail_follows_targets_and_allocation_not_just_detail_period(self):
        period = SimpleNamespace(name="PER-1", employer="EMP-1")
        remittances = [
            frappe._dict(name="R-DIRECT", detail_period="PER-1", allocation_detail="[]"),
            frappe._dict(name="R-TARGET", detail_period="", allocation_detail="[]"),
            frappe._dict(name="R-AUTO", detail_period="PER-2", allocation_detail='[{"periodo":"PER-1"}]'),
            frappe._dict(name="R-HISTORY", detail_period="", allocation_detail="[]"),
            frappe._dict(name="R-COMP", detail_period="", allocation_detail='[{"partida":"COMP-1"}]'),
            frappe._dict(name="R-OTHER", detail_period="PER-2", allocation_detail='[{"periodo":"PER-2"}]'),
        ]
        targets = [
            frappe._dict(parent="R-TARGET", period="PER-1", historical_application="", complementary_item=""),
            frappe._dict(parent="R-HISTORY", period="", historical_application="H-1", complementary_item=""),
            frappe._dict(parent="R-OTHER", period="PER-2", historical_application="", complementary_item=""),
        ]

        def get_all(doctype, *, filters, **kwargs):
            if doctype == "CN Remittance Allocation":
                self.assertEqual(filters["employer"], "EMP-1")
                self.assertIn("Parcial; saldo sin detalle", filters["detail_status"][1])
                return remittances
            if doctype == "CN Remittance Target":
                return targets
            if doctype == "CN Source Row":
                self.assertEqual(filters["historical_period"], "PER-1")
                return ["H-1"]
            if doctype == "CN Complementary Item":
                self.assertEqual(filters["period"], "PER-1")
                return ["COMP-1"]
            raise AssertionError(doctype)

        with patch.object(period_module.frappe, "get_all", side_effect=get_all):
            self.assertEqual(
                period_module._pending_remittance_details_for_period(period),
                ["R-AUTO", "R-COMP", "R-DIRECT", "R-HISTORY", "R-TARGET"],
            )

    def test_import_reconciliation_is_triggered_when_sources_exist(self):
        path = (
            "credinomina_reconciliation.conciliacion_credinomina.doctype"
            ".cn_source_import.cn_source_import.reconcile_all_sources"
        )
        with patch.object(period_module.frappe, "db", SimpleNamespace(exists=lambda *_: True)), \
             patch(path, return_value={"matched": 1}) as reconcile:
            self.assertEqual(period_module._reconcile_if_sources(), {"matched": 1})
        reconcile.assert_called_once_with()

    def test_reconciliation_runs_when_remittance_precedes_collection(self):
        path = (
            "credinomina_reconciliation.conciliacion_credinomina.doctype"
            ".cn_source_import.cn_source_import.reconcile_all_sources"
        )
        db = SimpleNamespace(exists=lambda doctype, *_: doctype == "CN Remittance Allocation")
        with patch.object(period_module.frappe, "db", db), \
             patch(path, return_value={"cash": 1}) as reconcile:
            self.assertEqual(period_module._reconcile_if_sources(), {"cash": 1})
        reconcile.assert_called_once_with()

    def test_comment_change_reconciles_even_with_only_confirmed_remittance(self):
        previous = SimpleNamespace(collection_rows=[frappe._dict(
            name="ROW-1", first_exception_comment="Antes", application_comment="",
        )])
        current = SimpleNamespace(
            flags=frappe._dict(), collection_rows=[frappe._dict(
                name="ROW-1", first_exception_comment="Aclarado con empresa",
                application_comment="",
            )], get_doc_before_save=lambda: previous,
        )
        with patch.object(period_module, "_reconcile_if_sources", return_value={}) as reconcile:
            period_module.CNReconciliationPeriod.on_update(current)
        reconcile.assert_called_once_with()

    def test_loaded_operational_period_cannot_change_company_or_month(self):
        previous = SimpleNamespace(
            employer="Original", payroll_month="2026-09-01",
            collection_rows=[frappe._dict(name="ROW-1")],
            status="Cobranza cargada", collection_cycle="Mensual",
            reconciliation_mode="Operativa",
        )
        period = SimpleNamespace(
            name="PER-1", employer="Other", payroll_month="2026-09-01",
            reconciliation_mode="Operativa", collection_cycle="Mensual",
            get_doc_before_save=lambda: previous, is_new=lambda: False,
        )
        with patch.object(period_module.frappe, "throw", side_effect=ValueError) as reject:
            with self.assertRaises(ValueError):
                period_module.CNReconciliationPeriod._validate_mode(period)
        self.assertIn("No cambie empresa ni mes", reject.call_args.args[0])

        period.employer = "Original"
        period.payroll_month = "2026-10-01"
        with patch.object(period_module.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                period_module.CNReconciliationPeriod._validate_mode(period)


if __name__ == "__main__":
    unittest.main()
