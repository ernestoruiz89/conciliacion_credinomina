"""Collection files may omit identity resolved from the period month's portfolio."""
import io
import unittest
from datetime import date
from unittest.mock import patch

import frappe
from openpyxl import load_workbook

from credinomina_reconciliation import collection_portfolio as portfolio
from credinomina_reconciliation.parsers import SourceFileError, parse_collection_file
from credinomina_reconciliation.templates import build_template_xlsx


class CollectionPortfolioTests(unittest.TestCase):
    def record(self, **values):
        return dict(source_row=2, loan_number="12800", client_name="", client_number="",
                    national_id="", expected_usd=79.23) | values

    def row(self, **values):
        return frappe._dict(dict(credit_number="0012800-1", client_name="ANA PEREZ",
            client_number_core="769", national_id="CED-A", employer="EMP") | values)

    def enrich(self, records, rows=None, cuts=None, permission=True):
        if rows is None:
            rows = [self.row()]
        if cuts is None:
            cuts = [frappe._dict(name="SEP", report_date="2026-09-30")]
        with patch.object(portfolio.frappe, "has_permission", return_value=permission), \
             patch.object(portfolio.frappe, "get_list", return_value=cuts) as query, \
             patch.object(portfolio, "get_credit_rows", return_value=rows) as credit_query:
            result = portfolio.enrich_collection_records(records, "EMP", "2026-09-15")
        return result, query, credit_query

    def test_month_end_selected_for_midmonth_cutoff_and_siaf_identity_filled(self):
        record = self.record()
        result, query, credit_query = self.enrich([record])
        self.assertEqual(query.call_args.kwargs["filters"], {
            "disabled": 0, "docstatus": ["!=", 2],
            "status": ["in", ["Importado", "Importado con alertas"]],
            "report_date": ["<=", date(2026, 9, 30)],
        })
        self.assertEqual(query.call_args.kwargs["order_by"], "report_date desc, name desc")
        self.assertEqual(query.call_args.kwargs["limit_page_length"], 1)
        self.assertEqual(credit_query.call_args.kwargs["filters"]["parent"], "SEP")
        self.assertEqual((record["client_name"], record["client_number"], record["national_id"]),
                         ("ANA PEREZ", "769", "CED-A"))
        self.assertEqual((record["loan_number"], record["expected_usd"]), ("12800", 79.23))
        self.assertEqual(result["completed_rows"], 1)

    def test_previous_available_month_is_usable(self):
        result, _, _ = self.enrich([self.record()], cuts=[frappe._dict(name="AUG", report_date="2026-08-31")])
        self.assertEqual(result["snapshot"], "AUG")

    def test_missing_name_needs_accessible_cut_and_exact_credit(self):
        for options in ({"cuts": []}, {"rows": []}, {"permission": False},
                        {"rows": [self.row(credit_number="12800-2")]},
                        {"rows": [self.row(client_name="")]}):
            with self.subTest(options=options), self.assertRaisesRegex(SourceFileError, "Fila 2.*nombre"):
                self.enrich([self.record()], **options)

    def test_named_legacy_file_can_still_import_without_portfolio_access(self):
        record = self.record(client_name="ANA PEREZ", client_number="769")
        _, query, credit_query = self.enrich([record], permission=False)
        query.assert_not_called()
        credit_query.assert_not_called()

    def test_no_guessing_on_duplicate_credit_wrong_company_or_conflicting_identifiers(self):
        for rows, record, reason in (
            ([self.row(), self.row()], self.record(), "varias veces"),
            ([self.row(employer="OTHER")], self.record(), "empresa"),
            ([self.row()], self.record(client_number="999"), "número de cliente"),
            ([self.row()], self.record(national_id="CED-B"), "cédula"),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(SourceFileError, reason):
                self.enrich([record], rows=rows)

    def test_provided_name_and_identifiers_are_not_overwritten(self):
        record = self.record(client_name="PEREZ ANA", client_number="000769", national_id="CED-A")
        before = dict(record)
        result, _, _ = self.enrich([record])
        self.assertEqual(record, before)
        self.assertEqual(result["completed_rows"], 0)

    def test_missing_credit_has_actionable_error(self):
        with self.assertRaisesRegex(SourceFileError, "Fila 2.*Nro. Crédito"):
            self.enrich([self.record(loan_number="")])

    def test_minimal_csv_accepts_credit_and_amount_without_name_header(self):
        data = "Nro. Crédito,Monto de la cuota en US$\n12800,79.23\n".encode()
        records = parse_collection_file("collection.csv", data, require_identity=True)
        self.assertEqual(len(records), 1)
        self.enrich(records)
        self.assertEqual(records[0]["client_name"], "ANA PEREZ")

    def test_amount_without_any_identity_is_not_silently_skipped(self):
        data = "Nro. Crédito,Monto de la cuota en US$\n,79.23\n".encode()
        with self.assertRaisesRegex(SourceFileError, "fila 2.*identificación"):
            parse_collection_file("collection.csv", data, require_identity=True)

    def test_template_explains_optional_names_only_for_collection(self):
        for kind, expected in (("cobranza", "Opcional"), ("empresa", "Obligatorio")):
            workbook = load_workbook(io.BytesIO(build_template_xlsx(kind)))
            header = next(cell for cell in workbook.active[1] if cell.value == "Nombre y Apellidos del Cliente")
            self.assertIn(expected, header.comment.text)
            workbook.close()
