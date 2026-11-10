"""Offline checks for the downloadable reconciliation snapshot."""

from __future__ import annotations

import io
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from openpyxl import load_workbook

from credinomina_reconciliation.control_export import (
    build_control_workbook, _control_state, _collection_pending, monthly_rows,
)


class TestControlExport(unittest.TestCase):
    def workbook(self, **data):
        payload = build_control_workbook({"year": 2025, "periods": [], **data}, exceptions=[], actions=[],
            employer_label="Empresa", generated_at=datetime(2025, 12, 31))
        return load_workbook(io.BytesIO(payload))

    def test_projected_application_counts_once_and_uses_usd(self):
        payment = {"referencia": "DEP", "importe_usd": 60}
        application = dict(name="A", parent="IMP", historical_period="P", collection_row_id="C", event_type="Aplicacion",
            currency="NIO", amount=3650, manual_fx_rate=36.5, application_adjustment_usd=10, client_number="01",
            historical_detail=[payment], historical_remitted_usd=60, historical_balance_usd=30)
        collection = dict(name="C", parent="P", row_key="fila", applied_usd=90, expected_usd=90, remitted_usd=60,
            client_number="01", remittance_detail=[dict(payment, aplicacion_directa="A")])
        period = dict(name="P", employer="E", month="2025-01", reconciliation_mode="Operativa",
            applied_usd=90, applied_total_usd=100, remitted_total_usd=70, pending_usd=30,
            cash_breakdown_known=1, deposit_coverage_usd=60, complementary_coverage_usd=10,
            rows=[collection], historical_rows=[application])
        book = self.workbook(periods=[period])
        detail = book["Detalle cliente"]
        self.assertEqual([detail.cell(r, 15).value for r in (5, 6)], [0, 90])
        self.assertEqual([detail.cell(r, 17).value for r in (5, 6)], [0, -60])
        self.assertEqual([detail.cell(r, 19).value for r in (5, 6)], [0, 30])
        self.assertEqual((detail["X6"].value, detail["Y6"].value), (100, 10))
        self.assertEqual((book["Cruces"].max_row, book["Cruces"]["C5"].value), (5, "Operativa"))
        issues = list(book["Partidas y excepciones"].values)[4:]
        self.assertEqual([r[7] for r in issues if r[0] == "Aplicación sin depósito"], [30])

    def test_two_applications_of_same_loan_keep_shared_cash_at_period_level(self):
        applications = [dict(name=name, parent="IMP", historical_period="P", event_type="Aplicacion",
            currency="USD", amount=value, historical_remitted_usd=value, historical_balance_usd=0,
            historical_detail=[{"referencia": "D", "importe_usd": value}], client_number="001", loan_number="001-1")
            for name, value in (("A", 60), ("B", 40))]
        deposit = dict(allocated_usd=90, allocation_detail=[
            {"tipo": "Aplicacion historica", "periodo": "P", "aplicacion_id": row["name"], "importe_usd": row["amount"]}
            for row in applications] + [{"tipo": "Partida complementaria", "importe_usd": -10}])
        period = dict(name="P", employer="E", month="2025-01", reconciliation_mode="Historica", applied_usd=100,
            remitted_total_usd=100, pending_usd=0, cash_breakdown_known=1, deposit_coverage_usd=90,
            complementary_coverage_usd=10, negative_complementary_usd=-10, historical_rows=applications)
        with patch("frappe.has_permission", return_value=True), patch(
                "credinomina_reconciliation.reconciliation_report_cash.load_period_deposits", return_value={"D": deposit}):
            book = self.workbook(periods=[period])
        self.assertEqual((book["Períodos"]["P5"].value, book["Períodos"]["L5"].value), (-90, -10))
        for row in (5, 6):
            detail = book["Detalle cliente"]
            self.assertEqual([detail.cell(row, c).value for c in (16, 17, 19)], ["N/D", "N/D", 0])
        self.assertEqual(book["Cruces"].max_row, 6)

    def test_deposit_and_complement_reduce_the_balance_while_the_bank_receipt_is_positive(self):
        application = dict(name="A", parent="IMP", historical_period="P", event_type="Aplicacion",
            currency="USD", amount=100, historical_remitted_usd=100, client_number="001",
            historical_detail=[dict(referencia="D", importe_usd=100)],
            deposit_match_reason="Aplicación: 100.0 US$ aplicados al crédito; ajuste 0.0 US$; "
                                "100.0 US$ vinculados a depósitos; 0.0 US$ pendientes de evidencia de depósito.")
        period = dict(name="P", reconciliation_mode="Historica", applied_usd=100, remitted_total_usd=100,
            pending_usd=0, cash_breakdown_known=1, deposit_coverage_usd=90, complementary_coverage_usd=10,
            negative_complementary_usd=-10, historical_rows=[application])
        book = self.workbook(periods=[period], cash_deposits=[dict(name="D", total_usd=90)])
        for sheet, columns in (("Períodos", "KLPN"), ("Detalle cliente", "OPQS")):
            applied, complement, cash, pending = [book[sheet][col + "5"].value for col in columns]
            self.assertEqual((applied, complement, cash, pending), (100, -10, -90, 0))
            self.assertEqual(applied + complement + cash, pending)
        self.assertEqual(book["Depósitos"]["H5"].value, 90)
        note = book["Detalle cliente"]["U5"].value
        self.assertIn("100.00 − 10.00 − 90.00 = 0.00", note)
        self.assertNotIn("100.0 US$ vinculados a depósitos", note)

    def test_unassigned_missing_conversion_preserves_row_and_indeterminate_totals(self):
        rows = [dict(parent="I", name="A", currency="NIO", amount=365, equivalent_currency="USD",
            equivalent_amount=10, fx_basis="Equivalente del core", application_adjustment_usd=2),
            dict(parent="I", name="B", currency="NIO", amount=365)]
        book = self.workbook(unassigned_operational_applications=rows)
        for sheet, column in (("Aplicaciones sin período", "K"), ("Resumen mensual", "J")):
            self.assertIn("SUBTOTAL(103,", book[sheet][column + "3"].value)
        self.assertEqual([book["Aplicaciones sin período"][f"K{r}"].value for r in (5, 6)], [8, "N/D"])
        self.assertEqual(book["Resumen mensual"]["J5"].value, "N/D")
        self.assertEqual([book["Partidas y excepciones"][f"H{r}"].value for r in (5, 6)], [8, "N/D"])

    def test_company_cent_is_separate_from_customer_deposit_and_complement(self):
        proof = dict(deposit="D", employer="E", period="P", amount_usd=-.01, application_ids=["A"])
        application = dict(name="A", parent="I", historical_period="P", event_type="Aplicacion", currency="USD",
            amount=100, historical_remitted_usd=100, client_number="007",
            historical_detail=[dict(importe_usd=100, conversion_empresa=proof)])
        period = dict(name="P", employer="E", reconciliation_mode="Historica", applied_usd=100,
            remitted_total_usd=100, pending_usd=0, cash_breakdown_known=1, deposit_coverage_usd=99.99,
            complementary_coverage_usd=0, conversion_coverage_usd=.01, historical_rows=[application])
        book = self.workbook(periods=[period])
        detail = book["Detalle cliente"]
        self.assertEqual([detail[c + "5"].value for c in "DPQS"], ["007", 0, -100, 0])
        self.assertEqual([detail[c + "6"].value for c in "DPQT"], [None, 0, .01, "Diferencia de empresa"])
        self.assertEqual(book["Períodos"]["P5"].value, -99.99)
        self.assertIn("US$ 0.01", book["Períodos"]["T5"].value)

    def test_deposit_distribution_preserves_unidentified_portion_and_company_difference(self):
        from credinomina_reconciliation.control_deposits import build_cash_deposits
        deposit = dict(name="DEP", employer="E", amount_usd=99.99, allocated_usd=99.99,
            result="Conciliado con diferencia documentada", allocation_detail=[
                dict(tipo="Aplicacion historica", aplicacion_id="A", periodo="P", importe_usd=80),
                dict(tipo="Partida complementaria", partida="X", fila_detalle="F", cliente="Ana", importe_usd=7),
                dict(tipo="Partida complementaria", partida="X", importe_usd=13),
                dict(tipo="Diferencia documentada de empresa", importe_usd=-.01)])
        cash = build_cash_deposits([deposit], {"X": dict(name="X", category="Ajuste")},
            {"P": dict(name="P", employer="E")})
        self.assertFalse(cash[0]["needs_review"])
        book = self.workbook(cash_deposits=cash)
        receipt = book["Depósitos"]
        self.assertEqual([receipt[c + "5"].value for c in ("H", "I", "J")], [99.99, 79.99, 20])
        portions = list(book["Distribución depósitos"].values)[4:]
        self.assertEqual([(r[8], r[10]) for r in portions if r[3] == "Partida complementaria"], [("Ana", 7), (None, 13)])
        self.assertEqual(round(sum(r[10] for r in portions), 2), 99.99)

    def test_historical_missing_evidence_and_many_to_many_links(self):
        historical = {
            "name": "H-2025-04", "employer": "EMP-1", "employer_name": "Empresa Uno",
            "month": "2025-04", "reconciliation_mode": "Historica",
            "historical_scope": "Fecha exacta", "historical_application_date": "2025-04-15",
            "status": "Conciliado", "control_state": "historico_conciliado",
            "expected_usd": 0, "deducted_usd": 0, "applied_usd": 46.52,
            "complementary_usd": 0, "rounding_adjustment_usd": 0.01,
            "remitted_usd": 46.53, "historical_pending_usd": 0,
            "worker_gap_usd": 0, "employer_gap_usd": 0, "unclassified_deposit_usd": 0,
            "rows": [], "historical_rows": [{
                "name": "APP-1", "client_number": "001", "client_name": "=HYPERLINK(\"x\")",
                "loan_number": "L-1", "event_date": "2025-04-15", "reference": "AP-1",
                "amount": 46.52, "historical_remitted_usd": 46.53,
                "historical_balance_usd": 0, "deposit_match_status": "Depósito conciliado",
                "historical_detail": json.dumps([
                    {"referencia": "DEP-1", "fecha": "2025-05-03", "importe_usd": 20},
                    {"referencia": "DEP-2", "fecha": "2025-05-18", "importe_usd": 26.52},
                    {"referencia": "DEP-2", "fecha": "2025-05-18", "importe_usd": 0.01,
                     "diferencia_usd": 0.01, "origen": "Tolerancia automática"},
                ]),
            }],
            "exceptions": [], "surpluses": [],
        }
        operational = {
            "name": "O-2026-09", "employer": "EMP-1", "employer_name": "Empresa Uno",
            "month": "2026-09", "reconciliation_mode": "Operativa",
            "remittance_due_date": "2026-10-15", "control_state": "diferencia",
            "control_cut_on": "2026-09-30 18:00:00",
            "control_cut_note": "Solicitar pago parcial y detalle actualizado",
            "control_cut_summary": "Cobranza US$ 100.00; deducido US$ 90.00",
            "remark": "Deducción parcial por ingreso insuficiente",
            "expected_usd": 100, "deducted_usd": 90, "applied_usd": 90,
            "complementary_usd": 0, "rounding_adjustment_usd": 0,
            "remitted_usd": 90, "historical_pending_usd": 0,
            "worker_gap_usd": 10, "employer_gap_usd": 0, "unclassified_deposit_usd": 0,
            "rows": [{
                "row_key": "ROW-1", "client_number": "002", "client_name": "Ana López",
                "loan_number": "L-2", "expected_usd": 100, "deducted_usd": 90,
                "applied_usd": 90, "complementary_usd": 0,
                "rounding_adjustment_usd": 0, "remitted_usd": 90,
                "collection_shortfall_usd": 10, "application_reference": "AP-2",
                "remittance_detail": json.dumps([
                    {"referencia": "DEP-2", "fecha": "2026-10-16", "importe_usd": 90},
                ]),
            }], "historical_rows": [], "exceptions": [], "surpluses": [],
        }
        data = {
            "year": 2025, "periods": [historical, operational],
            "totals": {
                "expected_usd": 100, "deducted_usd": 90,
                "applied_usd": 136.52, "remitted_usd": 136.53,
                "complementary_usd": 0, "rounding_adjustment_usd": 0.01,
                "historical_pending_usd": 0, "worker_gap_usd": 10,
                "employer_gap_usd": 0, "unclassified_deposit_usd": 0,
            },
            "open_deposits": [], "unassigned_historical_applications": [],
            "cash_deposits": [{
                "name": "DEP-5-2025-0001", "employer": "EMP-1", "date": "2025-05-18", "month": "2025-05",
                "reference": "000123", "bank_account": "Banco Demo USD", "currency": "USD", "original_amount": 100,
                "total_usd": 100, "credits_usd": 46.53, "other_usd": 10, "adjustments_usd": 0,
                "credit_balance_usd": 30, "undetailed_credit_usd": 5, "unclassified_usd": 13.47, "review_usd": 0,
                "result": "Parcial con saldo a favor", "needs_review": True, "payroll_months": ["2025-04"],
                "destinations": [
                    {"type": "Créditos", "employer": "EMP-1", "label": "H-2025-04", "month": "2025-04", "amount_usd": 46.53,
                     "people": [{"client_name": "Cliente de prueba", "client_number": "001", "loan_number": "L-1", "amount_usd": 46.53}]},
                    {"type": "Partida complementaria", "employer": "EMP-1", "label": "Administración", "amount_usd": 10},
                    {"type": "Saldo a favor del cliente", "employer": "EMP-1", "label": "Reembolso pendiente", "amount_usd": 25,
                     "people": [{"client_name": "Cliente de prueba", "client_number": "001", "amount_usd": 25}]},
                ],
            }],
        }
        exceptions = [{
            "name": "EX-1", "period": "O-2026-09", "status": "Resuelta",
            "exception_type": "Deduccion parcial", "cause_category": "Ingreso insuficiente",
            "assigned_to": "operador@example.com", "next_action": "Confirmar saldo",
            "commitment_date": "2026-10-20", "description": "Faltan 10",
            "resolution": "Cliente notificado", "amount_usd": 10,
        }]
        actions = [{
            "parent": "EX-1", "action_at": "2026-10-18 09:00:00",
            "action_by": "operador@example.com", "action_type": "Contacto con empleado",
            "details": "Se llamó al cliente", "evidence_file": None,
            "external_reference": "TICKET-1",
        }]
        payload = build_control_workbook(
            data, exceptions=exceptions, actions=actions,
            employer_label="Empresa Uno", generated_at=datetime(2026, 9, 27, 12),
        )
        book = load_workbook(io.BytesIO(payload), data_only=False)
        self.assertEqual(book.sheetnames, [
            "Resumen", "Resumen mensual", "Períodos", "Detalle cliente", "Cruces", "Depósitos", "Distribución depósitos",
            "Aplicaciones sin período", "Partidas y excepciones", "Gestiones", "Guía",
        ])
        summary = book["Períodos"]
        self.assertEqual(summary["J5"].value, "N/D")
        self.assertEqual(summary["K5"].value, 46.52)
        self.assertEqual(summary["D5"].value, "Histórica")
        self.assertEqual(summary["I5"].value, "Conciliado")
        self.assertEqual(summary["I6"].value, "Conciliado")
        self.assertEqual(summary["N5"].value, 0)
        self.assertEqual(summary["N6"].value, 0)
        self.assertEqual(summary["L5"].value, 0.01)
        self.assertEqual(summary["H6"].value.year, 2026)
        self.assertEqual(summary["R6"].value.year, 2026)
        self.assertEqual(summary["S6"].value, "Solicitar pago parcial y detalle actualizado")
        self.assertEqual(summary["T6"].value, "Deducción parcial por ingreso insuficiente")
        self.assertEqual(summary["U6"].value, "Abierto")
        detail = book["Detalle cliente"]
        self.assertEqual(detail["N5"].value, "N/D")
        self.assertEqual(detail["O5"].value, 46.52)
        self.assertEqual(detail["F5"].data_type, "s")
        self.assertTrue(detail["F5"].value.startswith("'="))
        self.assertEqual(detail["P5"].data_type, "n")
        links = book["Cruces"]
        self.assertEqual(links.max_row, 8)
        self.assertEqual([links[f"M{row}"].value for row in range(5, 9)],
                         [20, 26.52, 0.01, 90])
        self.assertEqual(book["Partidas y excepciones"]["I6"].value, "Resuelta")
        self.assertEqual(book["Gestiones"]["A5"].value, "EX-1")
        self.assertEqual(sum(row[10].value for row in book["Distribución depósitos"].iter_rows(min_row=5)), 100)
        self.assertEqual(book["Depósitos"]["D5"].value, "000123")

    def test_same_status_labels_in_both_modes(self):
        for historical, operational, expected in (
            ("historico_excepcion", "diferencia", "Con diferencias"),
            ("historico_excedente", "excedente", "Con excedente"),
            ("historico_conciliado", "conciliado", "Conciliado"),
            ("historico_parcial", "parcial", "Parcial"),
            ("historico_pendiente", "en_transito", "Pendiente"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(_control_state(historical), expected)
                self.assertEqual(_control_state(operational), expected)

    def test_pending_includes_both_modes_without_netting_unrelated_balances(self):
        periods = [{
            "name": "H", "reconciliation_mode": "Historica",
            "control_state": "historico_parcial", "historical_pending_usd": 0,
            "historical_rows": [{"historical_balance_usd": 30}, {"historical_balance_usd": 0}],
        }, {
            "name": "O", "reconciliation_mode": "Operativa", "control_state": "parcial",
            "collection_cycle": "Mensual",
            "rows": [{
                "applied_usd": 90, "remitted_usd": 80,
                "remittance_detail": [{"importe_usd": 70},
                                      {"importe_usd": 10, "destino": "Partida complementaria"}],
            }, {"applied_usd": 10, "remittance_detail": [{"importe_usd": 50}]}],
        }]
        book = load_workbook(io.BytesIO(build_control_workbook(
            {"year": 2026, "periods": periods, "totals": {}},
            exceptions=[], actions=[], employer_label="Todas", generated_at=datetime(2026, 9, 30),
        )))
        summary = book["Períodos"]
        self.assertEqual(sum(row[7] for row in monthly_rows({"periods": periods})), 50)
        self.assertEqual([summary[f"N{r}"].value for r in (5, 6)], [30, 20])
        self.assertEqual(summary["E6"].value, "Mensual")
        detail = book["Detalle cliente"]
        self.assertEqual([detail[f"S{r}"].value for r in range(5, 9)], [30, 0, 20, 0])
        for sheet, header_row in ((summary, 4), (detail, 4), (book["Cruces"], 4)):
            for cell in sheet[header_row]:
                self.assertNotRegex(cell.value, r"(?i)históric|operativ")

    def test_rounding_adjustment_is_signed_and_fx_is_not_cash(self):
        for applied, paid, adjustment in ((46.52, 46.53, 0.01), (46.53, 46.52, -0.01)):
            self.assertEqual(_collection_pending({
                "applied_usd": applied, "rounding_adjustment_usd": adjustment,
                "remittance_detail": [{"importe_usd": paid}],
            }), 0)
        self.assertEqual(_collection_pending({"applied_usd": 10, "fx_variance_usd": 10}), 10)

    def test_empty_and_single_mode_exports_keep_common_headers_and_missing_data(self):
        for mode in (None, "Historica", "Operativa"):
            with self.subTest(mode=mode):
                book = load_workbook(io.BytesIO(build_control_workbook(
                    {"year": 2026, "periods": [{"name": "P", "reconciliation_mode": mode}] if mode else [], "totals": {}},
                    exceptions=[], actions=[], employer_label="Todas", generated_at=datetime(2026, 9, 30),
                )))
                summary = book["Períodos"]
                if mode:
                    self.assertEqual(summary["J5"].value, 0 if mode == "Operativa" else "N/D")
                    self.assertEqual(summary["N5"].value, 0)
                self.assertEqual(summary["N4"].value, "Aplicado pendiente de depósito USD")
                self.assertNotIn("Deducido USD", [cell.value for cell in summary[4]])
                self.assertEqual(summary.freeze_panes, "C5")

    def test_monthly_groups_cuts_keeps_cash_separate_and_does_not_net_customers(self):
        periods = [{"name": "P1", "employer": "A", "employer_name": "Empresa A", "month": "2025-04",
                    "status": "Cerrado", "reconciliation_mode": "Historica", "applied_usd": 100,
                    "historical_rows": [{"historical_remitted_usd": 120, "historical_balance_usd": 0}],
                    "exceptions": [{"name": "E1"}]},
                   {"name": "P2", "employer": "A", "employer_name": "Empresa A", "month": "2025-04",
                    "reconciliation_mode": "Operativa", "applied_usd": 50,
                    "rows": [{"applied_usd": 50, "remittance_detail": [{"importe_usd": 10},
                              {"importe_usd": 20, "destino": "Partida complementaria"}]}],
                    "exceptions": [{"name": "E1"}, {"name": "E2"}]}]
        deposit = {"name": "D1", "employer": "B", "month": "2025-05", "total_usd": 500,
                   "unclassified_usd": 20, "needs_review": True}
        rows = monthly_rows({"periods": periods, "cash_deposits": [deposit, deposit],
                             "unassigned_operational_applications": [{"name": "A1", "employer": "A",
                                "event_date": "2025-04-09", "amount": 60, "application_adjustment_usd": 10}]})
        april, may = rows
        self.assertEqual(april[0], "Empresa A")
        self.assertEqual(april[2:10], (2, 1, 150, 130, 0, 40, 2, 50))
        self.assertEqual(april[10:14], (0, 0, 0, 0))
        self.assertEqual(may[0], "B")
        self.assertEqual(may[2:10], (0, 0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(may[10:14], (1, 500, 20, 1))

    def test_no_period_exception_and_both_application_modes_are_visible(self):
        data = {"year": None, "periods": [], "totals": {},
                "unassigned_historical_applications": [{"name": "H1", "parent": "CONTA-H", "employer": "A", "amount": 100}],
                "unassigned_operational_applications": [{"name": "O1", "parent": "CONTA-O", "employer": "B", "amount": 50}]}
        book = load_workbook(io.BytesIO(build_control_workbook(data,
            exceptions=[{"name": "E1", "employer": "B", "amount_usd": 1, "status": "Abierta",
                         "commitment_date": "2026-09-01"}],
            actions=[{"parent": "E1", "action_type": "Registrar ajuste en el core"}],
            employer_label="Todas", generated_at=datetime(2026, 10, 2), date_format="mm-dd-yyyy")))
        self.assertEqual(book["Aplicaciones sin período"]["A5"].value, "CONTA-H")
        self.assertEqual(book["Aplicaciones sin período"]["A6"].value, "CONTA-O")
        self.assertEqual(book["Partidas y excepciones"]["D5"].value, "B")
        self.assertEqual(book["Partidas y excepciones"]["S5"].value, 31)
        self.assertEqual(book["Partidas y excepciones"]["M5"].number_format, "mm-dd-yyyy")
        self.assertEqual(book["Gestiones"]["C5"].value, "B")

    def test_monthly_does_not_report_missing_deductions_as_zero(self):
        period = {"employer": "A", "month": "2026-09", "reconciliation_mode": "Operativa",
                  "expected_usd": 100, "deducted_usd": 0,
                  "rows": [{"expected_usd": 100, "collection_shortfall_usd": None}]}
        row, = monthly_rows({"periods": [period]})
        self.assertEqual(row[14:], (100, "N/D", "N/D", 100))
        period["rows"].append({"expected_usd": 50, "deducted_usd": 40, "collection_shortfall_usd": 10})
        period["expected_usd"] = 150
        row, = monthly_rows({"periods": [period]})
        self.assertEqual(row[14:], (150, "N/D", 10, 100))


if __name__ == "__main__":
    unittest.main()
