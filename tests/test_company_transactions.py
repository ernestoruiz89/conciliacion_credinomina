import json
import unittest
from pathlib import Path
from unittest.mock import patch

import frappe

from credinomina_reconciliation.company_transactions import application_state, deposit_state, monthly_counts
from credinomina_reconciliation.conciliacion_credinomina.report.transacciones_por_empresa import transacciones_por_empresa as report


class CompanyTransactionsTests(unittest.TestCase):
    def test_month_counts_keep_duplicates_year_boundaries_and_all_months(self):
        records = [dict(employer="A", date="2025-01-01", state="Conciliado")] * 5
        records += [dict(employer="A", date="2025-02-01", state=state) for state in ("Conciliado", "Pendiente")]
        records += [dict(employer="A", date="2025-03-31", state="Parcial"),
                    dict(employer="A", date="2025-12-31", state="Pendiente"),
                    dict(employer="A", date="2026-01-01", state="Conciliado"),
                    dict(employer="B", date="2025-04-30", state="Pendiente"),
                    dict(employer=None, date="2025-01-15", state="Pendiente")]
        rows = {row["employer"]: row for row in monthly_counts(records, 2025)}
        self.assertEqual(rows["A"]["total"], 9)
        self.assertEqual((rows["A"]["m01"], rows["A"]["m01_state"]), (5, "Conciliado"))
        self.assertEqual((rows["A"]["m02"], rows["A"]["m02_state"]), (2, "Parcial"))
        self.assertEqual(rows["A"]["m03_state"], "Parcial")
        self.assertEqual(rows["A"]["m12_state"], "Pendiente")
        self.assertEqual((rows["A"]["m04"], rows["A"]["m04_state"]), (0, ""))
        self.assertEqual(rows[""]["m01"], 1)
        self.assertEqual(len([key for key in rows["A"] if len(key) == 3 and key.startswith("m")]), 12)

    def test_applications_use_cash_status_not_collection_match(self):
        parent = {"status": "Importado"}
        row = {"effective": 1, "match_status": "Conciliado"}
        self.assertEqual(application_state(row, parent), "Pendiente")
        for status in ("Depósito conciliado", "Aplicación compensada", "Conciliada: depósito + ajuste"):
            settled = row | {"deposit_match_status": status}
            self.assertEqual(application_state(settled, parent), "Conciliado")
            self.assertEqual(application_state(settled, {"status": "Borrador"}), "Pendiente")
            self.assertEqual(application_state(settled | {"effective": 0}, parent), "Pendiente")
            self.assertEqual(application_state(settled | {"match_status": "Ignorado"}, parent), "Pendiente")
        for data in ({"deposit_match_status": "Depósito parcial"}, {"application_adjustment_usd": -10}, {"historical_remitted_usd": 10}):
            self.assertEqual(application_state(row | data, parent), "Parcial")

    def test_deposits_require_confirmed_complete_distribution(self):
        row = {"docstatus": 1, "amount_usd": 100, "allocated_usd": 100, "result": "Conciliado"}
        self.assertEqual(deposit_state(row), "Conciliado")
        self.assertEqual(deposit_state(row | {"docstatus": 0}), "Pendiente")
        self.assertEqual(deposit_state(row | {"allocated_usd": 80}), "Parcial")
        self.assertEqual(deposit_state(row | {"allocated_usd": 0, "result": "Pendiente"}), "Pendiente")
        self.assertEqual(deposit_state(row | {"result": "Revisar detalle"}), "Parcial")
        self.assertEqual(deposit_state(row | {"allocated_usd": 110}), "Parcial")
        for result in ("Parcial con saldo a favor", "Saldo a favor documentado", "Conciliado con saldo a favor del cliente"):
            self.assertEqual(deposit_state(row | {"allocated_usd": 80, "justified_surplus_usd": 20, "result": result}), "Conciliado")
            self.assertEqual(deposit_state(row | {"allocated_usd": 70, "justified_surplus_usd": 20, "result": result}), "Parcial")

    def test_import_query_respects_permissions_counts_rows_not_parents_and_draft_business_status(self):
        for drafts in (0, 1):
            with self.subTest(drafts=drafts):
                parents = [frappe._dict(name="IMPORT", employer="A", status="Importado")]
                rows = [frappe._dict(name=str(index), parent="IMPORT", event_date="2025-04-30", event_type="Aplicacion",
                                    effective=1, match_status="Conciliado", deposit_match_status="Depósito conciliado") for index in range(3)]
                if drafts:
                    parents.append(frappe._dict(name="DRAFT", employer="A", status="Borrador"))
                    rows.append(frappe._dict(parent="DRAFT", event_date="2025-04-15", effective=1, deposit_match_status="Depósito conciliado"))
                with patch.object(frappe, "has_permission", return_value=True), \
                        patch.object(frappe, "get_list", return_value=parents) as listed, \
                        patch.object(frappe, "get_all", return_value=rows) as children:
                    columns, data, message, _, summary = report.execute({"year": 2025, "employer": "A", "include_drafts": drafts})
                self.assertEqual(data[0]["m04"], 3 + drafts)
                self.assertEqual(data[0]["m04_state"], "Parcial" if drafts else "Conciliado")
                self.assertEqual(len(columns), 14)
                self.assertEqual(listed.call_args.kwargs["filters"]["employer"], "A")
                self.assertEqual("Borrador" in listed.call_args.kwargs["filters"]["status"][1], bool(drafts))
                child_filter = children.call_args.kwargs["filters"]
                self.assertEqual(child_filter["parent"][1], [parent.name for parent in parents])
                self.assertEqual(child_filter["event_date"], ["between", ["2025-01-01", "2025-12-31"]])
                self.assertEqual(child_filter["event_type"], "Aplicacion")
                self.assertEqual(child_filter["parentfield"], "rows")
                self.assertEqual(sum(item["value"] for item in summary), 3 + drafts)

    def test_deposit_query_uses_deposit_date_payer_and_one_count_per_record(self):
        for drafts in (0, 1):
            rows = [frappe._dict(name="DEP", employer="PAYER", deposit_date="2025-05-01", docstatus=1,
                        amount_usd=100, allocated_usd=100, result="Conciliado")]
            with patch.object(frappe, "has_permission", return_value=True), \
                    patch.object(frappe, "get_list", return_value=rows) as listed, \
                    patch.object(frappe, "get_all") as children:
                data = report.execute({"year": 2025, "transaction_type": "Depósitos", "include_drafts": drafts})[1]
            self.assertEqual((data[0]["employer"], data[0]["m05"]), ("PAYER", 1))
            self.assertIn("deposit_date", listed.call_args.kwargs["filters"])
            self.assertEqual(listed.call_args.kwargs["filters"]["docstatus"], ["in", [0, 1]] if drafts else 1)
            children.assert_not_called()

    def test_no_permission_or_invalid_filters_never_load_data(self):
        for filters in ({"year": "2025 OR 1"}, {"year": 2025, "transaction_type": "Otro"},
                        {"year": 2025}, {"year": 2025, "transaction_type": "Depósitos"}):
            with patch.object(frappe, "has_permission", return_value=False), \
                    patch.object(frappe, "throw", side_effect=ValueError), \
                    patch.object(frappe, "get_list") as listed, self.assertRaises(ValueError):
                report.execute(filters)
            listed.assert_not_called()

    def test_empty_permission_scope_never_reads_child_table(self):
        with patch.object(frappe, "has_permission", return_value=True), \
                patch.object(frappe, "get_list", return_value=[]), patch.object(frappe, "get_all") as children:
            result = report.execute({"year": 2025})
        self.assertEqual(result[1], [])
        children.assert_not_called()

    def test_report_is_registered_in_workspace_with_operator_roles(self):
        root = Path(__file__).resolve().parents[1] / "credinomina_reconciliation"
        meta = json.loads((root / "conciliacion_credinomina/report/transacciones_por_empresa/transacciones_por_empresa.json").read_text())
        workspace = json.loads((root / "conciliacion_credinomina/workspace/conciliacion_credinomina/conciliacion_credinomina.json").read_text())
        self.assertEqual(meta["report_type"], "Script Report")
        self.assertEqual({row["role"] for row in meta["roles"]}, {"System Manager", "Operador Credinomina", "Supervisor Credinomina"})
        self.assertEqual(sum(row.get("link_to") == meta["name"] for row in workspace["links"]), 1)
        self.assertIn("add_company_transactions_report", (root / "patches.txt").read_text())

    def test_migration_loads_report_before_refreshing_workspace(self):
        from credinomina_reconciliation.patches.v1_0.add_company_transactions_report import execute
        with patch.object(frappe, "reload_doc") as reload, \
                patch("credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow.execute") as refresh:
            execute()
        reload.assert_called_once_with("conciliacion_credinomina", "report", "transacciones_por_empresa")
        refresh.assert_called_once_with()
