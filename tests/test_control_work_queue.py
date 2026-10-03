"""The work queue reports evidence gaps without inventing receivables."""

import unittest
from datetime import date, datetime
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import (
    _build_work_items, get_control_data,
)
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina import (
    control_credinomina,
)


class ControlWorkQueueTests(unittest.TestCase):
    def test_operational_period_shows_independent_evidence_and_follow_up(self):
        period = {
            "name": "P-SEP", "employer": "EMP", "employer_name": "Empresa A",
            "month": "2026-09", "collection_cycle": "Mensual",
            "reconciliation_mode": "Operativa", "pending_detail_usd": 80,
            "worker_gap_usd": 20, "employer_gap_usd": 50,
            "rows": [frappe._dict(application_status="Diferencia aplicacion vs deposito")],
            "historical_rows": [],
            "exceptions": [frappe._dict(
                name="EX-1", commitment_date="2026-09-20", amount_usd=5,
                next_action="Solicitar soporte",
            )],
        }
        items = _build_work_items(
            [period], [], [], [], [], [], {"EMP": "Empresa A"},
            as_of=date(2026, 9, 28),
        )
        kinds = {item["kind"] for item in items}
        self.assertEqual(kinds, {
            "company_detail", "employee_shortfall", "unlinked_remittance",
            "difference", "overdue_exception",
        })
        self.assertEqual(items[0]["kind"], "overdue_exception")
        self.assertEqual(items[0]["target_name"], "EX-1")
        self.assertEqual(next(item for item in items if item["kind"] == "company_detail")["amount_usd"], 80)
        self.assertIn("no es CxC confirmada", next(
            item for item in items if item["kind"] == "unlinked_remittance"
        )["next_action"])

    def test_deposit_detail_and_unassigned_cash_have_document_links(self):
        period = {
            "name": "P-MAY", "employer": "EMP", "employer_name": "Empresa A",
            "month": "2025-05", "reconciliation_mode": "Historica",
            "historical_pending_usd": 0, "rows": [], "historical_rows": [],
            "exceptions": [],
        }
        registered = [frappe._dict(
            name="REM-1", employer="EMP", deposit_reference="DEP-1",
            amount_usd=100, detail_count=0, detail_file=None,
            detail_periods=[], allocation_detail="[]",
        ), frappe._dict(
            name="REM-2", employer="EMP", deposit_reference="DEP-2",
            amount_usd=50, detail_count=1, detail_file="/private/files/detail.xlsx",
            detail_periods=[{"period": "P-MAY"}], allocation_detail="[]",
        )]
        deposits = [
            frappe._dict(parent="REM-1", source_doctype="CN Remittance Allocation",
                         reference="DEP-1", unclassified_usd=25, employer="EMP"),
            frappe._dict(parent="REM-2", source_doctype="CN Remittance Allocation",
                         reference="DEP-2", unclassified_usd=25, employer="EMP"),
            frappe._dict(parent="IMP-1", reference="OTHER-1", unclassified_usd=10),
            frappe._dict(parent="IMP-1", reference="OTHER-2", unclassified_usd=5),
        ]
        links = [frappe._dict(parent="REM-1", period="P-MAY")]
        items = _build_work_items(
            [period], deposits, registered, links, [], [], {"EMP": "Empresa A"},
            as_of=date(2026, 9, 28),
        )
        by_kind = {item["kind"]: item for item in items}
        self.assertEqual(by_kind["deposit_detail"]["target_name"], "REM-1")
        self.assertEqual(by_kind["deposit_detail"]["period"], "P-MAY")
        self.assertEqual(by_kind["unassigned_deposit"]["amount_usd"], 25)
        self.assertEqual(by_kind["unassigned_deposit"]["target_name"], "REM-2")
        self.assertEqual(by_kind["unassigned_deposit"]["period"], "P-MAY")
        self.assertEqual(by_kind["classify_bank"]["count"], 2)
        self.assertEqual(by_kind["classify_bank"]["amount_usd"], 15)
        self.assertEqual(by_kind["classify_bank"]["employer_name"], "Sin empresa confirmada")
        self.assertIn("otro cliente", by_kind["classify_bank"]["next_action"])

    def test_unassigned_applications_do_not_claim_an_employer_or_convert_currency(self):
        historical = [frappe._dict(parent="IMP-H", amount=20, currency="USD")]
        operational = [frappe._dict(parent="IMP-O", amount=370, amount_usd=None,
                                   currency="NIO")]
        items = _build_work_items(
            [], [], [], [], historical, operational, {}, as_of=date(2026, 9, 28),
        )
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["period"] is None for item in items))
        self.assertTrue(all(item["employer_name"] == "Sin empresa confirmada" for item in items))
        self.assertEqual(next(item for item in items if item["kind"] == "historical_application")["amount_usd"], 20)
        self.assertIsNone(next(item for item in items if item["kind"] == "operational_application")["amount_usd"])

    def test_unassigned_deposit_uses_allocation_periods_without_manual_targets(self):
        periods = [
            {"name": "P-DEC", "employer": "EMP", "employer_name": "Empresa A",
             "month": "2026-12", "collection_cycle": "Mensual", "reconciliation_mode": "Operativa",
             "rows": [], "historical_rows": [], "exceptions": []},
            {"name": "P-NOV", "employer": "EMP", "employer_name": "Empresa A",
             "month": "2026-11", "collection_cycle": "Mensual", "reconciliation_mode": "Operativa",
             "rows": [], "historical_rows": [], "exceptions": []},
        ]
        remittance = frappe._dict(
            name="REM-JAN", employer="EMP", deposit_reference="DEP-JAN",
            amount_usd=100, detail_count=2, detail_file="/private/files/detail.xlsx",
            detail_periods=[], detail_status="Cargado", result="Parcial",
            allocation_detail='[{"periodo":"P-NOV"},{"periodo":"P-DEC"}]',
            unclassified_usd=20,
        )
        deposit = frappe._dict(
            parent="REM-JAN", source_doctype="CN Remittance Allocation",
            reference="DEP-JAN", employer="EMP", unclassified_usd=20,
        )
        items = _build_work_items(
            periods, [deposit], [remittance], [], [], [], {"EMP": "Empresa A"},
            as_of=date(2027, 1, 20),
        )
        self.assertEqual([item["kind"] for item in items], ["unassigned_deposit"])
        self.assertIsNone(items[0]["period"])
        self.assertIn("2026-11", items[0]["period_label"])
        self.assertIn("2026-12", items[0]["period_label"])

    def test_fully_manual_distribution_is_not_mislabeled_missing_detail(self):
        remittance = frappe._dict(
            name="REM-MANUAL", employer="EMP", deposit_reference="DEP-MANUAL",
            amount_usd=100, detail_count=0, detail_file=None, detail_periods=[],
            detail_status="Distribución manual", result="Conciliado",
            allocation_detail="[]",
        )
        items = _build_work_items(
            [], [], [remittance], [], [], [], {"EMP": "Empresa A"},
            as_of=date(2026, 9, 28),
        )
        self.assertEqual(items, [])

        remittance.detail_status = "Detalle pendiente"
        items = _build_work_items(
            [], [], [remittance], [], [], [], {"EMP": "Empresa A"},
            as_of=date(2026, 9, 28),
        )
        self.assertEqual([item["kind"] for item in items], ["deposit_detail"])

        # A fully classified company credit has no loan payment to itemize.
        remittance.result = "Saldo a favor documentado"
        remittance.justified_surplus_usd = 100
        remittance.unclassified_usd = 0
        items = _build_work_items(
            [], [], [remittance], [], [], [], {"EMP": "Empresa A"},
            as_of=date(2026, 9, 28),
        )
        self.assertEqual(items, [])

    def test_invalid_manual_destinations_take_priority_over_missing_file(self):
        remittance = frappe._dict(
            name="REM-BAD-TARGET", employer="EMP", deposit_reference="DEP-BAD",
            amount_usd=100, detail_count=0, detail_file=None, detail_periods=[],
            detail_status="Detalle pendiente", result="Revisar destinos",
            allocated_usd=0, justified_surplus_usd=0, unclassified_usd=100,
            allocation_detail="[]",
        )
        deposit = frappe._dict(
            parent="REM-BAD-TARGET", source_doctype="CN Remittance Allocation",
            reference="DEP-BAD", employer="EMP", unclassified_usd=100,
        )
        items = _build_work_items(
            [], [deposit], [remittance], [], [], [], {"EMP": "Empresa A"},
            as_of=date(2026, 9, 28),
        )
        self.assertEqual([item["kind"] for item in items], ["review_targets"])
        self.assertIn("destinos manuales", items[0]["next_action"])
        self.assertIsNone(items[0]["amount_usd"])

    def test_manual_payment_plus_documented_surplus_needs_no_file(self):
        remittance = frappe._dict(
            name="REM-CREDIT", employer="EMP", deposit_reference="DEP-CREDIT",
            amount_usd=110, detail_count=0, detail_file=None, detail_periods=[],
            detail_status="Detalle pendiente", result="Parcial con saldo a favor",
            allocated_usd=100, justified_surplus_usd=10, unclassified_usd=0,
            allocation_detail="[]",
        )
        deposit = frappe._dict(
            parent="REM-CREDIT", source_doctype="CN Remittance Allocation",
            reference="DEP-CREDIT", employer="EMP", unclassified_usd=0,
        )
        for status in (
            "Detalle pendiente", "Parcial; saldo sin detalle",
            "Distribución manual; excedente documentado",
        ):
            remittance.detail_status = status
            with self.subTest(status=status):
                self.assertEqual(_build_work_items(
                    [], [deposit], [remittance], [], [], [], {"EMP": "Empresa A"},
                    as_of=date(2026, 9, 28),
                ), [])

    def test_problematic_imported_detail_is_one_review_task(self):
        remittance = frappe._dict(
            name="REM-REVIEW", employer="EMP", deposit_reference="DEP-REVIEW",
            amount_usd=100, detail_count=2, detail_file="/private/files/detail.xlsx",
            detail_periods=[], detail_status="Revisar filas", result="Revisar detalle",
            unclassified_usd=40, allocation_detail="[]",
        )
        deposit = frappe._dict(
            parent="REM-REVIEW", source_doctype="CN Remittance Allocation",
            reference="DEP-REVIEW", employer="EMP", unclassified_usd=40,
        )
        items = _build_work_items(
            [], [deposit], [remittance], [], [], [], {"EMP": "Empresa A"},
            as_of=date(2026, 9, 28),
        )
        self.assertEqual([item["kind"] for item in items], ["review_deposit_detail"])
        self.assertEqual(items[0]["target_name"], "REM-REVIEW")
        self.assertEqual(items[0]["amount_usd"], 100)

    def test_settled_evidence_does_not_create_work(self):
        period = {
            "name": "P-OK", "employer": "EMP", "month": "2026-09",
            "reconciliation_mode": "Operativa", "pending_detail_usd": 0,
            "worker_gap_usd": 0, "employer_gap_usd": 0,
            "rows": [frappe._dict(application_status="Aplicado y remitido")],
            "historical_rows": [], "exceptions": [],
        }
        self.assertEqual(_build_work_items(
            [period], [], [], [], [], [], {}, as_of=date(2026, 9, 28),
        ), [])

    def test_empty_year_is_valid_with_remittance_read_permission(self):
        with patch.object(control_credinomina.frappe, "has_permission", return_value=True), \
             patch.object(control_credinomina.frappe, "get_list", return_value=[]), \
             patch.object(control_credinomina.frappe, "get_all", return_value=[]):
            data = get_control_data(year=2026)
        self.assertEqual(data["periods"], [])
        self.assertEqual(data["work_items"], [])

    def test_default_year_and_overdue_day_use_site_clock(self):
        captured = []

        def get_list(doctype, **kwargs):
            if doctype == "CN Reconciliation Period" and not kwargs.get("group_by"):
                captured.append(kwargs["filters"]["payroll_month"])
            return []

        with patch.object(control_credinomina.frappe, "has_permission", return_value=True), \
             patch.object(control_credinomina.frappe, "get_list", side_effect=get_list), \
             patch.object(control_credinomina.frappe, "get_all", return_value=[]), \
             patch.object(control_credinomina, "now_datetime",
                          return_value=datetime(2026, 12, 31, 23, 30)):
            data = get_control_data()
        self.assertEqual(data["year"], 2026)
        self.assertEqual(captured, [["between", ["2026-01-01", "2026-12-31"]]])

        period = {
            "name": "P-1", "employer": "EMP", "month": "2026-12",
            "reconciliation_mode": "Historica", "rows": [], "historical_rows": [],
            "exceptions": [frappe._dict(
                name="EX-OVERDUE", commitment_date="2026-12-30",
                amount_usd=1, next_action="Llamar",
            )],
        }
        with patch.object(control_credinomina, "now_datetime",
                          return_value=datetime(2026, 12, 31, 23, 30)):
            items = _build_work_items([period], [], [], [], [], [], {})
        self.assertEqual([item["kind"] for item in items], ["overdue_exception"])

    def test_unassigned_historical_routing_uses_row_import_and_date(self):
        imports = [
            frappe._dict(name="IMP-FLAG", historical_backfill=1, historical_period=None),
            frappe._dict(name="IMP-ROW", historical_backfill=0, historical_period=None),
            frappe._dict(name="IMP-DATE", historical_backfill=0, historical_period=None),
            frappe._dict(name="IMP-DEFAULT", historical_backfill=0, historical_period="H-SEP"),
            frappe._dict(name="IMP-OPERATIVE", historical_backfill=1, historical_period=None),
            frappe._dict(name="IMP-PLAIN", historical_backfill=0, historical_period=None),
        ]
        rows = [
            frappe._dict(parent="IMP-FLAG", event_date="2026-09-15", reference="FLAG",
                         client_name="Ana Pérez", processing_route="", amount=10, amount_usd=10, currency="USD"),
            frappe._dict(parent="IMP-ROW", event_date="2026-09-15", reference="ROW",
                         processing_route="Historica", amount=10, amount_usd=10, currency="USD"),
            frappe._dict(parent="IMP-DATE", event_date="2026-08-15", reference="DATE",
                         processing_route="", amount=10, amount_usd=10, currency="USD"),
            frappe._dict(parent="IMP-DEFAULT", event_date="2026-09-15", reference="DEFAULT",
                         processing_route="", amount=10, amount_usd=10, currency="USD"),
            frappe._dict(parent="IMP-OPERATIVE", event_date="2026-09-15", reference="OVERRIDE",
                         processing_route="Operativa", amount=10, amount_usd=10, currency="USD"),
            frappe._dict(parent="IMP-PLAIN", event_date="2026-09-15", reference="PLAIN",
                         processing_route="", amount=10, amount_usd=10, currency="USD"),
        ]

        def get_list(doctype, **_kwargs):
            if doctype == "CN Accounting Import":
                self.assertEqual(
                    _kwargs["filters"]["status"],
                    ["in", ["Importado", "Importado con excepciones"]],
                )
            return imports if doctype == "CN Accounting Import" else []

        def get_all(doctype, **kwargs):
            if doctype == "CN Source Row" and kwargs["filters"].get("event_type") == "Aplicacion":
                if "historical_period" in kwargs["filters"]:
                    self.assertIn("client_name", kwargs["fields"])
                return rows
            return []

        with patch.object(control_credinomina.frappe, "has_permission",
                          side_effect=lambda doctype, *_args: doctype in {
                              "CN Reconciliation Period", "CN Accounting Import",
                          }), \
             patch.object(control_credinomina.frappe, "get_list", side_effect=get_list), \
             patch.object(control_credinomina.frappe, "get_all", side_effect=get_all):
            data = get_control_data(year=2026)

        historical = {row.reference for row in data["unassigned_historical_applications"]}
        self.assertEqual(data["unassigned_historical_applications"][0].client_name, "Ana Pérez")
        operative = {row.reference for row in data["unassigned_operational_applications"]}
        self.assertEqual(historical, {"FLAG", "ROW", "DATE", "DEFAULT"})
        self.assertEqual(operative, {"OVERRIDE", "PLAIN"})
        self.assertEqual({item["kind"] for item in data["work_items"]}, {
            "historical_application", "operational_application",
        })


if __name__ == "__main__":
    unittest.main()
