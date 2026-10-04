import json
import unittest
from pathlib import Path
from unittest.mock import patch

import frappe

from credinomina_reconciliation.company_transactions import application_state, deposit_state, monthly_counts, application_month_amounts, deposit_month_amounts
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
        for data in ({"deposit_match_status": "Depósito parcial"}, {"application_adjustment_usd": 10}, {"historical_remitted_usd": 10}):
            self.assertEqual(application_state(row | data, parent), "Parcial")
        self.assertEqual(application_state(row | {"application_adjustment_usd": -10}, parent), "Pendiente")

    def test_amount_percentage_is_weighted_not_based_on_count(self):
        imports = {"I": {"status": "Importado", "employer": "A"}}
        rows = [dict(name=str(index), parent="I", event_date="2025-04-30", event_type="Aplicacion", currency="USD", amount=1,
                     effective=1, match_status="Conciliado", deposit_match_status="Depósito conciliado") for index in range(9)]
        rows.append(dict(rows[0], name="BIG", amount=991, deposit_match_status="Pendiente"))
        amounts = application_month_amounts(rows, imports, {})
        self.assertEqual(amounts[("A", "2025-04")], {"total_usd": 1000, "covered_usd": 9})
        records = [dict(employer="A", date=row["event_date"], state=application_state(row, imports["I"])) for row in rows]
        result = monthly_counts(records, 2025, amounts)[0]
        self.assertEqual(result["m04"], 10)
        self.assertEqual(result["m04_percentage"], 0.9)
        self.assertFalse(result["m04_half_covered"])

    def test_deposit_threshold_uses_exact_cents_and_drafts_add_no_coverage(self):
        for paid, yellow in (("49.99", False), ("50", True), ("50.01", True)):
            row = dict(employer="A", deposit_date="2025-04-30", docstatus=1, amount_usd=100, allocated_usd=paid)
            amounts = deposit_month_amounts([row])
            matrix = monthly_counts([dict(employer="A", date=row["deposit_date"], state="Parcial")], 2025, amounts)[0]
            self.assertEqual(matrix["m04_half_covered"], yellow)
        amounts = deposit_month_amounts([row, dict(row, docstatus=0, allocated_usd=100)])
        self.assertEqual(amounts[("A", "2025-04")]["total_usd"], 200)
        self.assertEqual(float(amounts[("A", "2025-04")]["covered_usd"]), 50.01)
        matrix = monthly_counts([dict(employer="A", date="2025-04-30", state="Parcial")], 2025,
            {("A", "2025-04"): {"total_usd": 100001, "covered_usd": 50000}})[0]
        self.assertFalse(matrix["m04_half_covered"])

    def test_historical_cash_and_positive_compensation_count_once(self):
        imports = {"I": {"status": "Importado", "employer": "A", "historical_period": "P"}}
        row = dict(name="R", parent="I", event_date="2025-04-30", event_type="Aplicacion", currency="USD", amount=100,
                   effective=1, match_status="Conciliado", deposit_match_status="Depósito parcial", client_number="1",
                   application_adjustment_usd=20, historical_remitted_usd=30)
        self.assertEqual(application_month_amounts([row], imports, {})[("A", "2025-04")], {"total_usd": 100, "covered_usd": 50})
        row.update(application_adjustment_usd=25, historical_remitted_usd=0)
        self.assertEqual(application_month_amounts([row], imports, {})[("A", "2025-04")]["covered_usd"], 25)
        row.update(application_adjustment_usd=-25, historical_remitted_usd=50)
        self.assertEqual(application_month_amounts([row], imports, {})[("A", "2025-04")], {"total_usd": 125, "covered_usd": 50})

    def test_operative_cash_is_counted_once_and_shared_cross_month_cash_is_unknown(self):
        imports = {"I": {"status": "Importado", "employer": "A"}}
        row = dict(name="R", parent="I", event_date="2025-04-30", event_type="Aplicacion", currency="USD", amount=100,
                   effective=1, match_status="Conciliado", deposit_match_status="Depósito parcial", client_number="1", collection_row_id="C")
        collections = {"C": dict(applied_usd=100, remittance_detail='[{"importe_usd":60},{"destino":"Partida complementaria","importe_usd":10}]')}
        self.assertEqual(application_month_amounts([row], imports, collections)[("A", "2025-04")], {"total_usd": 100, "covered_usd": 60})
        collections["C"]["applied_usd"] = 200
        amounts = application_month_amounts([row, dict(row, name="R2", event_date="2025-05-15")], imports, collections)
        self.assertIsNone(amounts[("A", "2025-04")]["covered_usd"])
        self.assertIsNone(amounts[("A", "2025-05")]["covered_usd"])
        amounts = application_month_amounts([row, dict(row, name="R2")], imports, collections)
        self.assertEqual(amounts[("A", "2025-04")], {"total_usd": 200, "covered_usd": 60})

    def test_conversion_and_missing_fx_do_not_invent_percentages(self):
        imports = {"I": {"status": "Importado", "employer": "A"}}
        row = dict(name="R", parent="I", event_date="2025-04-30", event_type="Aplicacion", currency="NIO", amount=3662.43,
                   manual_fx_rate=36.6243, effective=1, match_status="Conciliado", deposit_match_status="Depósito conciliado")
        self.assertEqual(application_month_amounts([row], imports, {})[("A", "2025-04")], {"total_usd": 100, "covered_usd": 100})
        self.assertIsNone(application_month_amounts([dict(row, manual_fx_rate=0)], imports, {})[("A", "2025-04")]["total_usd"])

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
                self.assertEqual(data[-1]["_is_total"], True)
                self.assertEqual(data[-1]["m04"], 3 + drafts)
                self.assertEqual(data[-1]["total"], sum(item["value"] for item in summary))
                self.assertEqual(data[0]["_detail_filters"], dict(year=2025, employer="A", transaction_type="Aplicaciones", include_drafts=drafts))

    def test_detail_uses_same_month_scope_permissions_states_and_cash(self):
        parent = frappe._dict(name="IMPORT", employer="A", status="Importado", historical_period="P")
        rows = [frappe._dict(name="R", parent="IMPORT", idx=2, event_type="Aplicacion", event_date="2024-02-29",
            effective=1, match_status="Conciliado", deposit_match_status="Depósito parcial", currency="USD", amount=100,
            application_adjustment_usd=20, historical_remitted_usd=30, client_number="C1", client_name="Ana", loan_number="L1")]
        with patch.object(frappe, "has_permission", return_value=True), \
                patch.object(frappe, "get_list", return_value=[parent]) as listed, \
                patch.object(frappe, "get_all", return_value=rows) as children:
            result = report.get_month_detail("2024", "2", "A", state="Parcial", search="ana")
        self.assertEqual(listed.call_args.kwargs["filters"]["employer"], "A")
        self.assertEqual(children.call_args.kwargs["filters"]["event_date"], ["between", ["2024-02-01", "2024-02-29"]])
        self.assertEqual(result["filtered_count"], 1)
        row = result["rows"][0]
        self.assertEqual((row["net_usd"], row["assigned_usd"], row["pending_usd"]), (80, 30, 50))
        self.assertEqual((row["document"], row["name"], row["row_index"]), ("IMPORT", "R", 2))
        self.assertEqual(result["summary"][1]["value"], 1)

    def test_deposit_detail_paginates_filters_and_preserves_credit_separately(self):
        rows = [frappe._dict(name=f"DEP-{index:03}", employer="A", deposit_date="2025-04-01", docstatus=1,
                            amount_usd=100, allocated_usd=80, justified_surplus_usd=20, result="Conciliado con saldo a favor",
                            deposit_reference=f"REF-{index:03}") for index in range(105)]
        with patch.object(frappe, "has_permission", return_value=True), patch.object(frappe, "get_list", return_value=rows), \
                patch.object(frappe, "get_all") as children:
            first = report.get_month_detail(2025, 4, "A", transaction_type="Depósitos")
            last = report.get_month_detail(2025, 4, "A", transaction_type="Depósitos", start=100)
            found = report.get_month_detail(2025, 4, "A", transaction_type="Depósitos", search="REF-104")
            empty = report.get_month_detail(2025, 4, "A", transaction_type="Depósitos", state="Pendiente")
        self.assertEqual(len(first["rows"]), 100)
        self.assertEqual(len(last["rows"]), 5)
        self.assertEqual(first["total"], 105)
        self.assertEqual(found["filtered_count"], 1)
        row = found["rows"][0]
        self.assertEqual((row["assigned_usd"], row["surplus_usd"], row["pending_usd"]), (80, 20, 0))
        self.assertEqual(empty["rows"], [])
        self.assertEqual(empty["summary"][0]["value"], 105)
        children.assert_not_called()

    def test_detail_never_exposes_shared_operative_cash_as_individual_cash(self):
        parent = frappe._dict(name="I", employer="A", status="Importado")
        source = frappe._dict(name="R", parent="I", event_type="Aplicacion", event_date="2025-04-30",
            effective=1, match_status="Conciliado", deposit_match_status="Depósito parcial", currency="USD", amount=100,
            client_number="1", collection_row_id="C")
        collections = {"C": dict(applied_usd=200, remittance_detail='[{"importe_usd":60}]')}
        with patch.object(frappe, "has_permission", return_value=True), \
                patch.object(report, "load_transactions", return_value=([dict(name="R", state="Parcial")], [source], {"I": parent})), \
                patch.object(report, "load_partial_collections", return_value=collections):
            row = report.get_month_detail(2025, 4, "A")["rows"][0]
        self.assertIsNone(row["assigned_usd"])
        self.assertIsNone(row["pending_usd"])
        self.assertIn("compartida", row["observations"])

    def test_detail_invalid_filters_and_permission_fail_before_loading(self):
        for values in (dict(year=2025, month=13), dict(year="sql", month=1), dict(year=2025, month=1, state="Otro"),
                       dict(year=2025, month=1, transaction_type="Otro"), dict(year=2025, month=1)):
            with patch.object(frappe, "has_permission", return_value=False), \
                    patch.object(frappe, "throw", side_effect=ValueError), \
                    patch.object(report, "load_transactions") as loader, self.assertRaises(ValueError):
                report.get_month_detail(employer="A", **values)
            loader.assert_not_called()

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
