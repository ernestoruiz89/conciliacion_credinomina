import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as source


class Record(dict):
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__

    def as_dict(self):
        return dict(self)


class CompanyReconciliationTests(unittest.TestCase):
    def test_company_engine_limits_every_persisted_group(self):
        row = Record(name="RA", event_type="Aplicacion", source_row=2, idx=1,
                     client_name="Ana", loan_number="101-1")
        imports = {
            name: Record(name=name, employer=employer, rows=rows,
                         exception_count=0, save=Mock(), check_permission=Mock(), recalculate_summary=Mock())
            for name, employer, rows in [("IA", "A", [row]), ("IB", "B", [])]
        }
        periods = {name: Record(name=name, employer=employer, reconciliation_mode="Historica",
                               collection_rows=[], status="Borrador")
                   for name, employer in [("PA", "A"), ("PB", "B")]}
        queries = []

        def get_all(doctype, **kwargs):
            queries.append((doctype, kwargs))
            if doctype == "CN Accounting Import":
                return list(imports)
            if doctype == "CN Reconciliation Period":
                if kwargs.get("pluck"):
                    return [p.name for p in periods.values() if p.employer == kwargs["filters"]["employer"]]
                return list(periods.values())
            if doctype == "CN Employer":
                return [Record(name=name, employer_name=name) for name in ("A", "B")]
            return []

        def match(rows, *_args):
            for item in rows:
                item.match_status = "Conciliado"
                item.deposit_match_status = "Depósito conciliado"

        allocation = {"rounding_movements": [], "registered_ids": {}, "deposit_meta": {}}
        with (
            patch.object(source.frappe, "has_permission", return_value=True),
            patch.object(source.frappe, "get_all", side_effect=get_all),
            patch.object(source.frappe, "get_doc", side_effect=lambda dt, name: (imports if dt == "CN Accounting Import" else periods)[name]),
            patch.object(source, "_deduplicate_applications"),
            patch.object(source, "_allocate_complementary_items", return_value={}),
            patch.object(source, "_operative_links", return_value={}),
            patch.object(source, "_registered_deposit_pairs", return_value=([], {})),
            patch.object(source, "_refresh_recognition_evidence"),
            patch.object(source, "_match_applications", side_effect=match),
            patch.object(source, "_distribute_deposits", return_value=allocation),
            patch.object(source, "_rebuild_period_balances") as rebuild,
            patch.object(source, "_rebuild_historical_balances") as history,
        ):
            result = source._reconcile_sources("A")
        imports["IA"].save.assert_called_once()
        imports["IB"].save.assert_not_called()
        imports["IA"].check_permission.assert_called_once_with("write")
        self.assertEqual([p.name for p in history.call_args.args[0]], ["PA"])
        self.assertEqual(rebuild.call_args.args[0], [])
        item_filters = [kwargs["filters"]["category"] for dt, kwargs in queries if dt == "CN Complementary Item"]
        self.assertIn(["not in", ["Saldo a favor de la empresa", "Diferencia por tolerancia"]], item_filters)
        self.assertIn("Saldo a favor de la empresa", item_filters)
        self.assertIn("Diferencia por tolerancia", item_filters)
        for doctype in ("CN Complementary Item", "CN Remittance Allocation"):
            query = next(kwargs for dt, kwargs in queries if dt == doctype)
            self.assertEqual(query["filters"]["employer"], "A", doctype)
        self.assertEqual((result["imports"], result["rows"], result["matched"], result["pending"]), (1, 1, 1, 0))

    def test_unassigned_import_with_existing_company_links_blocks_before_writes(self):
        document = Record(name="OLD", rows=[Record(historical_period="PA")])
        with (
            patch.object(source.frappe, "get_all", side_effect=[
                [Record(name="PA", employer="A")], [],
            ]),
            patch.object(source, "attach_employer_aliases"),
            patch.object(source, "_", side_effect=lambda text: text),
            patch.object(source.frappe, "throw", side_effect=lambda message: (_ for _ in ()).throw(ValueError(message))),
        ):
            with self.assertRaisesRegex(ValueError, "OLD"):
                source._company_imports([document], "A")

    def test_feedback_counts_are_disjoint_and_explain_pending_rows(self):
        base = dict(event_type="Aplicacion", _source_import="IA", source_row=2, client_name="Ana")
        rows = [
            Record(**base, match_status="Conciliado", deposit_match_status="Depósito conciliado"),
            Record(**base, match_status="Conciliado", deposit_match_status="Sin deposito", deposit_match_reason="Faltan US$10"),
            Record(**base, match_status="Sin coincidencia", match_reason="Asigne período"),
            Record(**base, match_status="Ignorado", match_reason="Duplicado"),
        ]
        with patch.object(source, "_", side_effect=lambda text: text):
            result = source._reconciliation_feedback(rows)
        self.assertEqual((result["matched"], result["pending"], result["ignored"]), (1, 2, 1))
        self.assertEqual(result["pending_rows"][0]["reason"], "Faltan US$10")
        self.assertIn("Asigne período", result["pending_rows"][1]["reason"])
        self.assertEqual(len(result["matched_rows"]), 1)
        self.assertEqual(result["matched_rows"][0]["reason"], "Aplicación y depósito conciliados.")

    def test_feedback_limits_matched_preview_without_losing_totals(self):
        rows = [Record(event_type="Aplicacion", _source_import="IA", source_row=i + 1,
                       client_name="Ana", match_status="Conciliado",
                       deposit_match_status="Depósito conciliado") for i in range(125)]
        with patch.object(source, "_", side_effect=lambda text: text):
            result = source._reconciliation_feedback(rows)
        self.assertEqual(result["matched"], 125)
        self.assertEqual(len(result["matched_rows"]), 100)
        self.assertEqual(result["pending_rows"], [])

    def test_endpoint_checks_document_permission_before_running(self):
        document = Record(check_permission=Mock(side_effect=PermissionError))
        with patch.object(source.frappe, "get_doc", return_value=document), patch.object(source, "_reconcile_sources") as run:
            with self.assertRaises(PermissionError):
                source.reconcile_company_sources("IA")
        run.assert_not_called()

    def test_rounding_reversal_only_queries_selected_company(self):
        movement = Record(status="Vigente", name="MA", period="PA")
        document = Record(save=Mock())
        with (
            patch.object(source.frappe, "get_all", return_value=[movement]) as query,
            patch.object(source.frappe, "get_doc", return_value=document) as get_doc,
            patch.object(source.frappe, "db", Mock(get_value=Mock(return_value="Abierto"))),
            patch.object(source, "now_datetime", return_value="2026-09-29 00:00:00"),
            patch.object(source, "_", side_effect=lambda text: text),
        ):
            source._sync_rounding_movements([], {}, [], employer="A")
        self.assertEqual(query.call_args.kwargs["filters"], {"category": "Diferencia por tolerancia", "docstatus": 1, "employer": "A"})
        get_doc.assert_called_once_with("CN Complementary Item", "MA")
        self.assertEqual(document.status, "Revertido")
