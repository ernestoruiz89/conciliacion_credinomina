import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import monthly_first_reconciliation as api
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as controller


def period(**values):
    return frappe._dict(dict(name="P", employer="E", payroll_month="2026-10-01", modified="p1",
        reconciliation_mode="Operativa", docstatus=0, status="Pendiente", collection_rows=[{}], check_permission=Mock()) | values)


def source(name="A", **values):
    return frappe._dict(dict(name=name, event_type="Aplicacion", currency="USD", effective=1,
        event_date="2026-10-15", amount_usd=50) | values)


def imported(name="I", **values):
    return frappe._dict(dict(name=name, employer="E", docstatus=0, status="Importado", modified="i1",
        rows=[source()], check_permission=Mock(), save=Mock(), add_comment=Mock()) | values)


class MonthlyFirstReconciliationTests(unittest.TestCase):
    def test_month_listing_has_no_page_or_current_list_scope(self):
        with patch.object(api, "_permissions"), patch.object(frappe, "get_list", return_value=[dict(name="P")]) as query:
            self.assertEqual(api.list_periods("2026-10-22"), [dict(name="P")])
        self.assertEqual(str(query.call_args.kwargs["filters"]["payroll_month"]), "2026-10-01")
        self.assertEqual(query.call_args.kwargs["limit_page_length"], 0)
        self.assertEqual(set(query.call_args.kwargs["filters"]), {"payroll_month", "docstatus"})

    def test_permissions_and_changed_month_are_checked(self):
        doc = period()
        with patch.object(frappe, "has_permission") as permissions, patch.object(frappe, "get_doc", return_value=doc), \
             patch.object(frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                api._period("P", "2026-09-01")
        self.assertEqual(permissions.call_count, 2)
        doc.check_permission.assert_called_once_with("write")

    def test_ineligible_periods_are_reported_without_reconciling(self):
        for values, state in [({"status": "Cerrado"}, "Cerrado"), ({"docstatus": 2}, "Cancelado"),
            ({"reconciliation_mode": "Historica"}, "No aplica conciliación 1"),
            ({"no_collection": 1}, "No aplica conciliación 1"), ({"collection_rows": []}, "Sin cobranza")]:
            with self.subTest(state=state), patch.object(api, "_period", return_value=period(**values)), \
                 patch.object(api, "_assigned_count") as count, patch.object(controller, "reconcile_first") as reconcile:
                result = api.reconcile_period("P", "2026-10-01")
                self.assertEqual(result["state"], state)
                self.assertFalse(result["can_assign"])
                count.assert_not_called()
                reconcile.assert_not_called()

    def test_missing_assignments_clear_old_matches_and_offer_assignment(self):
        with patch.object(api, "_period", return_value=period()), patch.object(api, "_assigned_count", return_value=0), \
             patch.object(engine, "_reconcile_sources") as reconcile:
            result = api.reconcile_period("P", "2026-10-01")
        self.assertEqual(result["state"], "Sin aplicaciones asignadas")
        self.assertTrue(result["can_assign"])
        reconcile.assert_called_once_with("E", preserve_deposits=True, period_name="P")

    def test_conformity_is_based_on_payroll_quality_not_cash_status(self):
        for conforming, state in [(2, "Conforme"), (1, "Con diferencias")]:
            with self.subTest(state=state), patch.object(api, "_period", return_value=period()), \
                 patch.object(api, "_assigned_count", return_value=2), patch.object(controller, "reconcile_first",
                    return_value=dict(reviewed=2, conforming=conforming, differences=2-conforming, missing_basis=0)) as reconcile:
                self.assertEqual(api.reconcile_period("P", "2026-10-01")["state"], state)
                reconcile.assert_called_once_with("P")

    def test_candidates_are_unassigned_editable_operating_applications(self):
        current = imported(rows=[source(), source("ASSIGNED", historical_period="OTHER"),
            source("DEPOSIT", event_type="Deposito"), source("OLD", event_date="2025-01-01"),
            source("INACTIVE", effective=0)])
        earlier = imported("EARLIER", rows=[source("SEPT", event_date="2026-09-15")])
        inherited = imported("INHERITED", historical_period="OTHER")
        hidden = imported("READONLY")
        docs = {row.name: row for row in [current, earlier, inherited, hidden]}
        with patch.object(api, "_period", return_value=period()), patch.object(frappe, "get_list", return_value=list(docs)), \
             patch.object(frappe, "get_doc", side_effect=lambda dt, name: docs[name]), \
             patch.object(frappe, "has_permission", side_effect=lambda dt, perm, doc: doc.name != "READONLY"):
            result = api.get_unassigned_imports("P")
        self.assertEqual([row["name"] for row in result["imports"]], ["I", "EARLIER"])
        self.assertEqual(result["imports"][0]["applications"], ["A"])
        self.assertEqual(result["imports"][0]["amount_usd"], 50)
        self.assertTrue(result["imports"][0]["same_month"])
        self.assertFalse(result["imports"][1]["same_month"])

    def assign(self, doc=None, selections=None, current_period=None):
        doc, target = doc or imported(), current_period or period()
        selected = selections if selections is not None else [dict(name=doc.name, modified=doc.modified, applications=["A"])]
        with patch.object(api, "_period", return_value=target), \
             patch("credinomina_reconciliation.deposit_reconciliation.lock_cash_pool"), \
             patch("credinomina_reconciliation.paying_employers.reconciliation_companies", return_value=["E"]), \
             patch.object(frappe, "get_doc", side_effect=lambda dt, name, **kw: target if dt == api.PERIOD else doc), \
             patch.object(frappe, "throw", side_effect=ValueError), \
             patch.object(api, "reconcile_period", return_value={"state": "Conforme"}) as reconcile:
            result = api.assign_and_reconcile("P", "p1", selected)
            reconcile.assert_called_once_with("P", "2026-10-01")
            return result

    def test_assignment_sets_default_for_whole_import_and_reprocesses(self):
        doc = imported()
        self.assertEqual(self.assign(doc)["assigned_now"], 1)
        self.assertEqual(doc.historical_period, "P")
        self.assertEqual(doc.rows[0].historical_period, "P")
        doc.save.assert_called_once_with()
        doc.check_permission.assert_called_once_with("write")

    def test_mixed_import_preserves_other_row_assignments(self):
        doc = imported(rows=[source(), source("OTHER", historical_period="OTHER-PERIOD")])
        self.assign(doc)
        self.assertFalse(doc.historical_period)
        self.assertEqual(doc.rows[1].historical_period, "OTHER-PERIOD")

    def test_stale_forbidden_changed_or_injected_selections_are_rejected(self):
        for values, selections in [({"employer": "OTHER"}, None), ({"status": "Borrador"}, None),
            ({"historical_period": "OTHER"}, None), ({}, []), ({}, [dict(name="I", modified="old", applications=["A"])]),
            ({}, [dict(name="I", modified="i1", applications=["FORGED"])]),
            ({}, [dict(name="I", modified="i1", applications=["A", "A"])])]:
            with self.subTest(values=values, selections=selections):
                doc = imported(**values)
                with self.assertRaises(ValueError):
                    self.assign(doc, selections)
                doc.save.assert_not_called()
        with self.assertRaises(ValueError):
            self.assign(current_period=period(modified="changed"))

    def test_assignment_keeps_normal_save_guards(self):
        doc = imported(save=Mock(side_effect=ValueError("Cash protected")))
        with self.assertRaisesRegex(ValueError, "Cash protected"):
            self.assign(doc)
