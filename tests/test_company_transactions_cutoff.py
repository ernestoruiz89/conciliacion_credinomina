import unittest
from datetime import date
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.report.transacciones_por_empresa import transacciones_por_empresa as report


class CompanyTransactionsCutoffTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("has_permission", True),):
            active = patch.object(frappe, name, return_value=value)
            active.start(); self.addCleanup(active.stop)
        history = patch.object(report.reconciliation_trace, "histories", return_value={})
        history.start(); self.addCleanup(history.stop)

    def fixture(self, kind):
        sources = []
        for index, day in enumerate(("2025-03-31", "2025-04-14", "2025-04-15", "2025-04-16", "2025-05-01")):
            row = frappe._dict(name=str(index), parent="I", employer="A", event_date=day, deposit_date=day,
                idx=index + 1, docstatus=1, event_type="Aplicacion", amount=100, currency="USD", effective=1,
                match_status="Conciliado", historical_period="P", historical_remitted_usd=50,
                amount_usd=100, allocated_usd=50, result="Parcial", client_name="Prueba", deposit_reference="BANK")
            row._progress = dict(state="Parcial", original_usd=100, resolved_usd=50, pending_usd=50)
            row._balance = dict(used_label="Ajustado", management_pending_usd=None, management_status="")
            sources.append(row)
        parents = {"I": frappe._dict(name="I", employer="A", status="Importado", historical_period="P")}
        def loader(kind, drafts, scoped, dates, detail=False):
            selected = [row for row in sources if dates[0] <= row.event_date <= dates[1]]
            return ([dict(name=row.name, employer="A", date=row.event_date, state="Parcial") for row in selected], selected, parents)
        return sources, loader

    def test_inclusive_cutoff_matches_matrix_cards_drilldown_and_money_for_every_type(self):
        for kind in report.TRANSACTION_DOCTYPES:
            with self.subTest(kind=kind):
                _, loader = self.fixture(kind)
                with patch.object(report, "load_transactions", side_effect=loader), \
                     patch.object(report, "load_application_collections", return_value={}):
                    result = report.execute(dict(year=2025, transaction_type=kind, cutoff_date=date(2025, 4, 15)))
                    detail = report.get_month_detail(2025, 4, "A", transaction_type=kind, cutoff_date="2025-04-15",
                                                     state="Parcial", only_differences=1)
                    future = report.get_month_detail(2025, 5, "A", transaction_type=kind, cutoff_date="2025-04-15")
                matrix = result[1][0]
                self.assertEqual((matrix["m03"], matrix["m04"], matrix["m05"], matrix["total"]), (1, 2, 0, 3))
                self.assertEqual(result[1][-1]["total"], 3)
                self.assertEqual(sum(card["value"] for card in result[4]), 3)
                self.assertEqual(matrix["_detail_filters"]["cutoff_date"], "2025-04-15")
                self.assertEqual([row["date"] for row in detail["rows"]], ["2025-04-14", "2025-04-15"])
                self.assertEqual(detail["filtered_count"], 2)
                self.assertEqual(sum(card["value"] for card in detail["summary"]), 2)
                self.assertEqual(detail["totals"]["original_usd"], 200)
                self.assertEqual(detail["totals"]["pending_usd"], 100)
                self.assertEqual(future["rows"], [])
                self.assertEqual(future["totals"]["original_usd"], 0)

    def test_empty_cutoff_preserves_annual_view_and_dates_outside_year_have_consistent_results(self):
        _, loader = self.fixture("Depósitos")
        with patch.object(report, "load_transactions", side_effect=loader):
            for cutoff, expected in (("", 5), ("2024-12-31", 0), ("2026-01-01", 5), ("2025-01-01", 0)):
                with self.subTest(cutoff=cutoff):
                    result = report.execute(dict(year=2025, transaction_type="Depósitos", cutoff_date=cutoff))
                    self.assertEqual(sum(card["value"] for card in result[4]), expected)
                    if expected:
                        self.assertEqual(result[1][-1]["total"], expected)
                    else:
                        self.assertEqual(result[1], [])

    def test_invalid_date_is_rejected_by_both_endpoints_before_loading(self):
        for cutoff in ("2025-02-30", "invalid", "2025-13-01"):
            with self.subTest(cutoff=cutoff), patch.object(frappe, "throw", side_effect=ValueError), \
                 patch.object(report, "load_transactions") as loader:
                with self.assertRaises(ValueError):
                    report.execute(dict(year=2025, cutoff_date=cutoff))
                with self.assertRaises(ValueError):
                    report.get_month_detail(2025, 4, "A", cutoff_date=cutoff)
                loader.assert_not_called()

    def test_cutoff_retains_annual_shared_application_evidence_for_current_states(self):
        sources, loader = self.fixture("Aplicaciones")
        for row in sources:
            row.historical_period = ""
            row.historical_remitted_usd = 0
            row.collection_row_id = "C"
        # This shared claim cannot be validated by hiding its later applications.
        parents = {"I": frappe._dict(name="I", employer="A", status="Importado")}
        def shared(*args, **kwargs):
            records, selected, _ = loader(*args, **kwargs)
            return records, selected, parents
        with patch.object(report, "load_transactions", side_effect=shared) as query, \
             patch.object(report, "load_application_collections", return_value={"C": dict(applied_usd=100,
                 remittance_detail='[{"importe_usd":100}]')}):
            matrix = report.execute(dict(year=2025, cutoff_date="2025-04-15"))[1][0]
            detail = report.get_month_detail(2025, 4, "A", cutoff_date="2025-04-15")
        self.assertEqual(query.call_args_list[0].args[3], ["2025-01-01", "2025-12-31"])
        self.assertEqual(query.call_args_list[1].args[3], ["2025-01-01", "2025-12-31"])
        self.assertEqual(matrix["m04_state"], "Revisar distribución")
        self.assertTrue(all(row["state"] == "Revisar distribución" for row in detail["rows"]))

    def test_pagination_and_search_keep_full_cutoff_totals(self):
        sources, _ = self.fixture("Depósitos")
        rows = [frappe._dict(sources[0], name=str(index), deposit_date="2025-04-15", event_date="2025-04-15",
                            deposit_reference="REF-" + str(index)) for index in range(105)]
        rows.append(frappe._dict(rows[0], name="LATER", deposit_date="2025-04-16", event_date="2025-04-16"))
        records = [dict(name=row.name, employer="A", date=row.deposit_date, state="Parcial") for row in rows]
        with patch.object(report, "load_transactions", return_value=(records, rows, {})):
            first = report.get_month_detail(2025, 4, "A", transaction_type="Depósitos", cutoff_date="2025-04-15")
            last = report.get_month_detail(2025, 4, "A", transaction_type="Depósitos", cutoff_date="2025-04-15", start=100)
            found = report.get_month_detail(2025, 4, "A", transaction_type="Depósitos", cutoff_date="2025-04-15", search="REF-104")
        self.assertEqual((first["filtered_count"], len(first["rows"]), len(last["rows"])), (105, 100, 5))
        self.assertEqual(first["totals"], last["totals"])
        self.assertEqual(first["totals"]["original_usd"], 10500)
        self.assertEqual(found["filtered_count"], 1)
        self.assertEqual(found["totals"]["original_usd"], 100)
