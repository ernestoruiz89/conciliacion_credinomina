import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openpyxl import load_workbook

from credinomina_reconciliation.deposit_identity import load_detail_loan_clients
from credinomina_reconciliation.paying_employers import choose_detail_client
from credinomina_reconciliation.parsers import SourceFileError, parse_collection_file
from credinomina_reconciliation.templates import build_template_xlsx
from credinomina_reconciliation.remittance_credit_selection import complete_detail_clients
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as remittance


class Row(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def set(self, key, value):
        self[key] = value

    def append(self, key, value):
        self.setdefault(key, []).append(Row(value))


class DepositIdentityTests(unittest.TestCase):
    def setUp(self):
        self.ana = dict(name="1", client_number="001", national_id="CED-1", employee_number="E1",
                        client_name="Ana María Pérez", client_aliases=["Ana Perez"], employer="EMP")
        self.bea = dict(name="2", client_number="002", national_id="CED-2", employee_number="E2",
                        client_name="Beatriz Ruiz", employer="EMP")
        self.clients = [self.ana, self.bea]
        self.loans = {"13375-1": {"1"}, "13376-1": {"2"}}

    def resolve(self, row, clients=None, loans=None):
        return choose_detail_client(row, self.clients if clients is None else clients,
                                    "EMP", {"EMP"}, self.loans if loans is None else loans)

    def test_each_identifier_alone_is_sufficient(self):
        for field, value in [("loan_number", "13375"), ("client_number", "1"),
                             ("national_id", "ced-1"), ("employee_number", "E1"),
                             ("client_name", "PÉREZ MARÍA ANA"), ("client_name", "Ana Perez")]:
            with self.subTest(field=field, value=value):
                client, _ = self.resolve({field: value})
                self.assertEqual(client["name"], "1")

    def test_all_supplied_fields_must_agree_including_name_and_credit(self):
        valid = {key: self.ana[key] for key in ("client_number", "national_id", "employee_number", "client_name")}
        valid["loan_number"] = "13375"
        self.assertEqual(self.resolve(valid)[0]["name"], "1")
        for field, value in [("loan_number", "13376"), ("client_number", "2"),
                             ("national_id", "CED-2"), ("employee_number", "E2"),
                             ("client_name", "Beatriz Ruiz"), ("national_id", "UNKNOWN")]:
            with self.subTest(field=field):
                client, reason = self.resolve(valid | {field: value})
                self.assertIsNone(client)
                self.assertTrue(reason.startswith("Conflicto"))

    def test_ambiguous_and_unknown_loans_never_override_other_identifiers(self):
        for loans in ({}, {"13375-1": {"1", "2"}}, {"13375-1": {"1", None}}):
            self.assertIsNone(self.resolve({"loan_number": "13375", "client_number": "1"}, loans=loans)[0])
        self.assertIsNone(self.resolve({"client_name": "Ana Perez"}, clients=[self.ana | {"employer": "OTHER"}])[0])
        self.assertIsNone(self.resolve({"employee_number": "E1"}, clients=[self.ana, self.bea | {"employee_number": "E1"}])[0])

    def test_completes_missing_name_and_number_but_never_replaces_source_data(self):
        row = Row(loan_number="13375", deducted_usd=20)
        complete_detail_clients([row], self.clients, "EMP", {"EMP"}, self.loans)
        self.assertEqual((row.client, row.client_name, row.client_number), ("1", "Ana María Pérez", "001"))
        conflict = Row(loan_number="13375", client_name="Beatriz Ruiz", client_number="002")
        complete_detail_clients([conflict], self.clients, "EMP", {"EMP"}, self.loans)
        self.assertFalse(conflict.client)
        self.assertEqual(conflict.client_name, "Beatriz Ruiz")
        self.assertEqual(conflict.client_number, "002")

    def test_real_frappe_detail_document_resolves_and_revalidates_credit(self):
        from frappe.model.base_document import BaseDocument
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_detail.cn_remittance_detail import CNRemittanceDetail

        for credit in ("13375", "13375-1"):
            for docstatus in (0, 1):
                with self.subTest(credit=credit, docstatus=docstatus):
                    with patch.object(BaseDocument, "_get_table_fields", return_value=[]), \
                            patch.object(CNRemittanceDetail, "init_valid_columns"):
                        row = CNRemittanceDetail(dict(doctype="CN Remittance Detail", loan_number=credit, docstatus=docstatus))
                    # A real Frappe document supports .get(), not row["loan_number"].
                    self.assertFalse(hasattr(row, "__getitem__"))
                    complete_detail_clients([row], self.clients, "EMP", {"EMP"}, self.loans)
                    self.assertEqual((row.client, row.client_name, row.client_number), ("1", "Ana María Pérez", "001"))
                    # Saving an existing deposit repeats validation on enriched child documents.
                    complete_detail_clients([row], self.clients, "EMP", {"EMP"}, self.loans)
                    self.assertEqual(row.client, "1")
                    row.loan_number = "13376-1"
                    complete_detail_clients([row], self.clients, "EMP", {"EMP"}, self.loans)
                    self.assertFalse(row.client)
                    self.assertIn("Conflicto", row.identity_reason)
                    self.assertEqual(row.client_number, "001")

    def test_minimal_csv_headers_accept_each_identifier_and_keep_zero_rows(self):
        for header in ("Nro. Crédito", "Nro Cédula", "Nro. Cliente", "Nro. Empleado", "Nombre y Apellidos del Cliente"):
            with self.subTest(header=header):
                rows = parse_collection_file("detail.csv", f"{header},Deducido US$\n123,20\n124,0\n".encode(),
                    require_identity=True, require_deduction=True, keep_zero_rows=True)
                self.assertEqual(len(rows), 2)
        for amount in ("20", "0"):
            with self.assertRaisesRegex(SourceFileError, "fila 2.*identificación"):
                parse_collection_file("detail.csv", f"Nro. Cliente,Deducido US$\n,{amount}\n".encode(),
                    require_identity=True, require_deduction=True, keep_zero_rows=True)

    def test_collection_still_requires_name_when_requested(self):
        with self.assertRaises(SourceFileError):
            parse_collection_file("collection.csv", b"Nombre del cliente,Nro. Cliente,Deducido US$\n,1,20\n",
                                  require_name=True)

    def test_template_and_metadata_no_longer_require_name(self):
        sheet = load_workbook(io.BytesIO(build_template_xlsx("deposito"))).active
        for cell in sheet[1]:
            if cell.value in ("Nro. Crédito", "Nro Cédula", "Nro. Cliente", "Nro. Empleado", "Nombre y Apellidos del Cliente"):
                self.assertIn("Ninguno es obligatorio individualmente", cell.comment.text)
        root = Path(__file__).resolve().parents[1]
        meta = json.loads((root / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_detail/cn_remittance_detail.json").read_text())
        self.assertFalse(next(field for field in meta["fields"] if field["fieldname"] == "client_name").get("reqd"))

    def test_import_credit_only_fills_identity_and_amount_without_reconciling(self):
        document = Row(name="DEP", doctype="CN Remittance Allocation", employer="EMP", deposit_date="2025-04-30",
            docstatus=0, detail_file="/private/files/detail.csv", targets=[], check_permission=Mock(), save=Mock())
        file_doc = Row(file_name="detail.csv", get_content=lambda: "Nro. Crédito,Deducido US$\n13375,20\n".encode())
        with patch.object(remittance.frappe, "get_doc", side_effect=lambda dt, name: document if dt == document.doctype else file_doc), \
                patch.object(remittance.frappe, "get_all", return_value=["FILE"]), \
                patch.object(remittance, "load_client_index", return_value=self.clients), \
                patch.object(remittance, "load_detail_loan_clients", return_value=self.loans), \
                patch.object(remittance, "allowed_employers", return_value={"EMP"}), \
                patch.object(remittance, "now_datetime", return_value="2026-10-04 10:00:00"):
            remittance.import_remittance_detail("DEP")
        row = document.detail_rows[0]
        self.assertEqual((row.client, row.client_name, row.client_number, row.loan_number), ("1", "Ana María Pérez", "001", "13375-1"))
        self.assertEqual((row.amount_usd, row.match_status, document.detail_total_usd), (20, "Pendiente", 20))
        self.assertEqual(document.targets, [])

    def test_loan_lookup_uses_scoped_portfolio_and_accounting_parent_company(self):
        import frappe
        calls = []
        def get_all(doctype, **kwargs):
            calls.append((doctype, kwargs))
            return {
                "CN Credit Portfolio Snapshot": ["CUT"],
                "CN Credit Portfolio Row": [Row(credit_number="13375-1", matched_client="1", client_number_core="1", employer="EMP")],
                "CN Accounting Import": [Row(name="IMPORT", employer="EMP")],
                "CN Source Row": [Row(parent="IMPORT", loan_number="13376-1", client="2", client_number="2")],
            }[doctype]
        with patch.object(frappe, "get_all", side_effect=get_all):
            result = load_detail_loan_clients([{"loan_number": "13375"}, {"loan_number": "13376-1"}], self.clients, {"EMP"})
        self.assertEqual(result, self.loans)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[1][1]["filters"]["employer"], ["in", ["EMP"]])
        self.assertEqual(calls[3][1]["filters"]["parent"], ["in", ["IMPORT"]])
        self.assertNotIn("employer", calls[3][1]["fields"])

    def test_conflicting_portfolio_link_is_not_trusted(self):
        import frappe
        with patch.object(frappe, "get_all", side_effect=[
            ["CUT"], [Row(credit_number="13375-1", matched_client="1", client_number_core="2", employer="EMP")], [],
        ]):
            loans = load_detail_loan_clients([{"loan_number": "13375"}], self.clients, {"EMP"})
        self.assertIsNone(self.resolve({"loan_number": "13375"}, loans=loans)[0])

    def test_reconciliation_revalidates_all_identifiers_before_suggesting_targets(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as source
        from credinomina_reconciliation.allocation import allocate_cash
        deposit = Row(name="DEP", employer="EMP", detail_file="detail.csv", detail_hash="HASH", detail_source_file="detail.csv")
        cash = [dict(id="DEP", group="EMP", amount_usd=20, currency="USD", bank_currency="USD")]
        claims = [dict(id="H:APP", kind="H", group="EMP", client_number="1", client_name="Ana María Pérez", loan_number="13375-1", amount_usd=20)]
        for conflict in (False, True):
            with self.subTest(conflict=conflict):
                row = Row(name="ROW", parent="DEP", loan_number="13375-1", deducted_usd=20,
                          client_number="2" if conflict else "", identity_reason="Identificador exacto")
                with patch.object(source.frappe, "get_all", return_value=[row]), \
                        patch("credinomina_reconciliation.remittance_periods.attach_periods"), \
                        patch("credinomina_reconciliation.client_credit.load_credits", return_value=[]), \
                        patch("credinomina_reconciliation.deposit_identity.load_detail_loan_clients", return_value=self.loans):
                    context = source._prepare_remittance_details([deposit], {"DEP": "DEP"}, cash, claims, [], {}, self.clients)
                result = allocate_cash(cash, claims, context["instructions"], context["blocked_deposits"])
                if conflict:
                    self.assertEqual(result["allocations"], [])
                    self.assertEqual(context["contexts"]["DEP"]["rows"][0]["status"], "Revisar")
                    self.assertIn("Conflicto", row.identity_reason)
                else:
                    self.assertEqual(len(result["allocations"]), 1)
                    self.assertEqual(result["deposit_remaining"]["DEP"], 0)
                    self.assertEqual(row.client_name, "Ana María Pérez")
