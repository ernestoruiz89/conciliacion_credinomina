import csv
import io
import unittest
from datetime import date
from unittest.mock import patch

import frappe
from credinomina_reconciliation.accounting_control import build_rows, summarize
from credinomina_reconciliation.parsers import parse_accounting_movements, apply_accounting_currency_override
from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables import execute
from credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables import control_mensual_de_movimientos_contables as report

DESCRIPTION = "REGISTRAMOS RELASIFICACION DE SALDOS A FAVOR DE LA CUENTA DE CONVENIO A LA 3001, DE LOS MESES DE NOVIEMBRE A DICIEMBRE 2025, SEGUN DETALLE-INGRIS MASSIEL HERNANDEZ AGROSACO 2426.73"


def source(name="ROW", parent="IMP", **changes):
    return {"name": name, "parent": parent, "idx": 1, "event_date": "2025-04-01", "source_account": "1602",
            "source_currency": "NIO", "source_debit": 3662.43, "source_credit": 0, "source_fx_rate": 36.6243,
            "accounting_source_key": "KEY", "description": "Resumen", "source_description": DESCRIPTION,
            "event_type": "Aplicacion", "application_adjustment_status": "Sin ajuste", **changes}


def imports():
    return {"IMP": {"name": "IMP", "employer": "Empresa", "currency": "NIO", "status": "Importado"}}


class AccountingControlTests(unittest.TestCase):
    def test_default_range_covers_current_month_including_leap_year(self):
        for today, last in [("2026-10-15", "2026-10-31"), ("2026-04-20", "2026-04-30"),
                            ("2026-02-10", "2026-02-28"), ("2024-02-10", "2024-02-29")]:
            with self.subTest(today=today), patch.object(report, "nowdate", return_value=today):
                self.assertEqual(report._date_range({}), [date.fromisoformat(today[:8] + "01"), date.fromisoformat(last)])

    def test_explicit_ranges_can_cross_months_years_or_be_one_day(self):
        for start, end in [("2025-04-15", "2025-06-10"), ("2025-12-31", "2026-01-01"), ("2025-04-15", "2025-04-15")]:
            self.assertEqual(report._date_range({"from_date": start, "to_date": end, "month": "2020-01-01"}),
                             [date.fromisoformat(start), date.fromisoformat(end)])

    def test_reject_incomplete_or_reversed_date_ranges(self):
        for filters in ({"from_date": "2025-04-15"}, {"to_date": "2025-04-15"},
                        {"from_date": "2025-04-16", "to_date": "2025-04-15"}):
            with self.subTest(filters=filters), patch.object(report, "_", side_effect=lambda value: value), \
                 patch.object(frappe, "throw", side_effect=ValueError):
                with self.assertRaises(ValueError):
                    report._date_range(filters)

    def test_description_preserved_without_truncation_in_parser_and_report(self):
        text = "  " + DESCRIPTION + "\n" + "Detalle " * 1000 + "  "
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["CUENTA_CONTABLE", "FECHA_APLICA", "TMOV", "TDOC", "NO_CMPTE", "DESCRIPCION", "DEBITO_DEL_MES", "CREDITO_DEL_MES"])
        writer.writerow(["1602", "2025-04-01", "01", "01", "100", text, "3662.43", "0"])
        records = apply_accounting_currency_override(parse_accounting_movements("x.csv", stream.getvalue().encode()), "NIO", 36.6243)
        self.assertEqual(records[0]["source_description"], text)
        self.assertEqual(records[0]["source_fx_rate"], 36.6243)
        rows, _ = build_rows([source(source_description=text)], imports(), [])
        self.assertEqual(rows[0]["description"], text)

    def test_convert_both_sides_independently_not_net_or_adjusted_amount(self):
        rows, _ = build_rows([source(source_credit=1831.22, amount=0, application_adjustment_usd=999)], imports(), [])
        self.assertEqual(rows[0]["debit_nio"], 3662.43)
        self.assertEqual(rows[0]["credit_nio"], 1831.22)
        self.assertEqual(rows[0]["debit_usd"], 100)
        self.assertEqual(rows[0]["credit_usd"], 50)
        self.assertEqual(rows[0]["net_usd"], 50)

    def test_mirror_counted_once_but_similar_lines_across_imports_remain(self):
        parents = imports()
        parents["IMP2"] = {"name": "IMP2", "currency": "NIO", "employer": "Empresa"}
        sources = [source(complementary_item="C"), source("ROW2", idx=2, complementary_item="C"), source("REPEAT", "IMP2")]
        complementary = {**source("C"), "source_date": "2025-04-01", "compensation_status": "Compensada totalmente"}
        rows, duplicates = build_rows(sources, parents, [complementary])
        self.assertEqual(len(rows), 3)
        self.assertEqual(duplicates, 2)
        self.assertEqual(sum(row["debit_usd"] for row in rows), 300)
        self.assertEqual(sum(row["state"] == "Compensada totalmente" for row in rows), 2)
        self.assertEqual(sum(bool(row["warning"]) for row in rows), 2)

    def test_standalone_bulk_items_unknown_company_and_usd_original(self):
        item = {**source("C", source_currency="USD", source_debit=0, source_credit=20),
                "source_date": "2025-04-01", "source_client_name": "Nombre conocido", "review_status": "No conciliatoria"}
        rows, _ = build_rows([], {}, [item, {"name": "MANUAL", "amount_usd": 100}])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["debit_nio"])
        self.assertEqual(rows[0]["credit_usd"], 20)
        self.assertEqual(rows[0]["client_name"], "Nombre conocido")
        self.assertEqual(rows[0]["employer"], "")
        self.assertEqual(rows[0]["description"], DESCRIPTION)

    def test_summary_keeps_original_currencies_separate_and_missing_fx_visible(self):
        rows, _ = build_rows([source(), source("USD", source_currency="USD", accounting_source_key="usd", source_debit=10),
                               source("MISSING", accounting_source_key="no-fx", source_fx_rate=0)], imports(), [])
        self.assertEqual(len(summarize(rows)), 2)
        nio = next(row for row in summarize(rows) if row["source_currency"] == "NIO")
        self.assertEqual(nio["debit_nio"], 7324.86)
        self.assertEqual(nio["debit_usd"], 100)
        self.assertEqual(nio["missing_conversion"], 1)
        self.assertTrue(next(row for row in rows if row["evidence_key"] == "no-fx")["warning"])

    def test_current_status_is_not_loan_matching_status(self):
        rows, _ = build_rows([source(match_status="Conciliado", deposit_match_status="Pendiente")], imports(), [])
        self.assertEqual(rows[0]["state"], "Pendiente")
        rows, _ = build_rows([source(deposit_match_status="Depósito parcial")], imports(), [])
        self.assertEqual(rows[0]["state"], "Parcialmente conciliado")

    def test_deposit_state_is_its_result_for_standalone_and_mirrored_movements(self):
        for docstatus in (0, 1, 2):
            for result in ("Pendiente", "Revisar detalle", "Conciliado", "Parcial con saldo a favor", ""):
                for mirrored in (False, True):
                    with self.subTest(docstatus=docstatus, result=result, mirrored=mirrored):
                        deposit = {**source("DEP", accounting_classification="Depósito"),
                                   "source_date": "2025-04-01", "docstatus": docstatus, "result": result}
                        sources = [source(accounting_classification="Depósito", deposit_match_status="Pendiente")] if mirrored else []
                        rows, _ = build_rows(sources, imports(), [], deposits=[deposit])
                        self.assertEqual(len(rows), 1)
                        self.assertEqual(rows[0]["state"], result)
                        self.assertEqual(rows[0]["remittance_allocation"], "DEP")

    def test_report_requires_both_read_permissions(self):
        with patch.object(frappe, "has_permission", return_value=False), patch.object(frappe, "throw", side_effect=PermissionError), \
             patch("credinomina_reconciliation.conciliacion_credinomina.report.control_mensual_de_movimientos_contables.control_mensual_de_movimientos_contables._", side_effect=lambda text: text):
            with self.assertRaises(PermissionError):
                execute({"month": "2025-04-01"})


if __name__ == "__main__":
    unittest.main()
