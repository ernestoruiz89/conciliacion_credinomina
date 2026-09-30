"""Offline checks for the downloadable reconciliation snapshot."""

from __future__ import annotations

import io
import json
import unittest
from datetime import datetime

from openpyxl import load_workbook

from credinomina_reconciliation.control_export import (
    build_control_workbook, _control_state, _collection_pending,
)


class TestControlExport(unittest.TestCase):
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
            "expected_usd": 100, "deducted_usd": 90, "applied_usd": 90,
            "complementary_usd": 0, "rounding_adjustment_usd": 0,
            "remitted_usd": 90, "historical_pending_usd": 0,
            "worker_gap_usd": 10, "employer_gap_usd": 0, "unclassified_deposit_usd": 0,
            "rows": [{
                "row_key": "ROW-1", "client_number": "002", "client_name": "Ana López",
                "loan_number": "L-2", "expected_usd": 100, "deducted_usd": 90,
                "applied_usd": 90, "complementary_usd": 0,
                "rounding_adjustment_usd": 0, "remitted_usd": 90,
                "employee_receivable_usd": 10, "application_reference": "AP-2",
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
            "Resumen", "Detalle cliente", "Cruces", "Partidas y excepciones", "Gestiones",
        ])
        summary = book["Resumen"]
        self.assertEqual(summary["J20"].value, "N/D")
        self.assertEqual(summary["K20"].value, "N/D")
        self.assertEqual(summary["D20"].value, "Histórica")
        self.assertEqual(summary["I20"].value, "Conciliado")
        self.assertEqual(summary["I21"].value, "Con diferencias")
        self.assertEqual(summary["P20"].value, 0)
        self.assertEqual(summary["P21"].value, 0)
        self.assertEqual(summary["B10"].value, 0.01)
        self.assertEqual(summary["H21"].value.year, 2026)
        self.assertEqual(summary["U21"].value.year, 2026)
        self.assertEqual(summary["V21"].value, "Solicitar pago parcial y detalle actualizado")
        self.assertIn("Cobranza US$ 100.00", summary["W21"].value)
        detail = book["Detalle cliente"]
        self.assertEqual(detail["N5"].value, "N/D")
        self.assertEqual(detail["O5"].value, "N/D")
        self.assertEqual(detail["F5"].data_type, "s")
        self.assertTrue(detail["F5"].value.startswith("'="))
        self.assertEqual(detail["P5"].data_type, "n")
        links = book["Cruces"]
        self.assertEqual(links.max_row, 8)
        self.assertEqual([links[f"M{row}"].value for row in range(5, 9)],
                         [20, 26.52, 0.01, 90])
        self.assertEqual(book["Partidas y excepciones"]["I6"].value, "Resuelta")
        self.assertEqual(book["Gestiones"]["A5"].value, "EX-1")

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
        summary = book["Resumen"]
        self.assertEqual(summary["B12"].value, 50)
        self.assertEqual([summary[f"P{r}"].value for r in (20, 21)], [30, 20])
        self.assertEqual(summary["E21"].value, "Mensual")
        detail = book["Detalle cliente"]
        self.assertEqual([detail[f"U{r}"].value for r in range(5, 9)], [30, 0, 20, 0])
        for sheet, header_row in ((summary, 19), (detail, 4), (book["Cruces"], 4)):
            for cell in sheet[header_row]:
                self.assertNotRegex(cell.value, r"(?i)históric|operativ")
        for row in summary.iter_rows(min_row=5, max_row=17, max_col=1):
            self.assertNotRegex(row[0].value or "", r"(?i)históric|operativ")

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
                summary = book["Resumen"]
                self.assertEqual(summary["B6"].value, 0 if mode == "Operativa" else "N/D")
                self.assertEqual(summary["B12"].value, 0)
                self.assertEqual(summary["P19"].value, "Aplicado pendiente de depósito USD")
                self.assertEqual(summary["B6"].number_format, 'General' if mode != "Operativa" else summary["B8"].number_format)


if __name__ == "__main__":
    unittest.main()
