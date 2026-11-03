import copy
import json
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import company_difference_repair as repair
from credinomina_reconciliation.company_conversion import KIND
from credinomina_reconciliation.deposit_difference import basis
from credinomina_reconciliation.patches.v1_0 import repair_company_difference_client_haircuts as migration
from credinomina_reconciliation.rounding import sum_money


class Row(frappe._dict):
    def as_dict(self):
        return dict(self)


class CompanyDifferenceRepairTests(unittest.TestCase):
    def fixture(self):
        deposit = Row(name="DEP", docstatus=1, apply_fifo=1, employer="EMP", amount_usd=99.99,
            allocated_usd=99.99, deposit_date="2026-03-13", deposit_reference="BANK", targets=[], detail_rows=[])
        sources, ledger = {}, []
        for name, full, paid in (("A", 60, 60), ("B", 40, 39.99)):
            part = dict(application_id=name, amount_usd=paid)
            target = dict(claim_id="H:" + name, amount_usd=paid, fifo_applications=[part])
            detail = Row(name="D" + name, amount_usd=full, linked_usd=paid, pending_usd=round(full-paid, 2),
                matched_targets=json.dumps([target]))
            deposit.detail_rows.append(detail)
            ledger.append(dict(tipo="Aplicacion historica", fila_detalle=detail.name, aplicacion_id=name,
                importe_usd=paid, periodo="P", aplicaciones_fifo=[part]))
            sources[name] = Row(name=name, historical_period="P", event_type="Aplicacion", effective=1,
                match_status="Conciliado", net_applied_usd=full, historical_remitted_usd=paid,
                historical_balance_usd=round(full-paid, 2), historical_detail=json.dumps([
                    dict(importe_usd=paid, referencia="BANK", comprobante="", fecha="2026-03-13")]))
        diff = Row(name="DIFF", row_kind="Diferencia documentada", difference_amount_usd=-.01,
            difference_reason="Manual", difference_notes="Conservar este comentario")
        deposit.detail_rows.append(diff)
        diff.difference_basis = basis(deposit, deposit.detail_rows)
        deposit.allocation_detail = json.dumps(ledger)
        return deposit, sources

    def test_exact_saved_client_gets_cent_and_net_bank_cash_stays_unchanged_without_fx_checks(self):
        deposit, sources = self.fixture()
        original = copy.deepcopy((deposit, sources))
        result = repair.plan(deposit, sources)
        self.assertEqual(result["gap_source"], "B")
        self.assertEqual(result["details"]["DB"]["linked_usd"], 40)
        self.assertEqual(result["sources"]["B"]["historical_balance_usd"], 0)
        self.assertEqual(result["sources"]["B"]["historical_remitted_usd"], 40)
        self.assertEqual(set(result["sources"]["A"]), {"historical_detail"})
        self.assertEqual(result["ledger"][-1]["tipo"], KIND)
        self.assertEqual(result["ledger"][-1]["importe_usd"], -.01)
        self.assertEqual(sum_money(row["importe_usd"] for row in result["ledger"]), sum_money([99.99]))
        self.assertEqual(result["proof"]["difference_reason"], "Manual")
        self.assertEqual((deposit, sources), original, "Planning must not change any source evidence")
        histories = [json.loads(row["historical_detail"])[0] for row in result["sources"].values()]
        self.assertTrue(all(row["conversion_empresa"] == result["proof"] for row in histories))
        self.assertEqual([row["importe_usd"] for row in histories], [60, 40])
        self.assertEqual(result["ledger"][0], json.loads(deposit.allocation_detail)[0])

    def test_already_repaired_ledger_is_idempotent(self):
        deposit, sources = self.fixture()
        deposit.allocation_detail = repair.plan(deposit, sources)["ledger"]
        self.assertIsNone(repair.plan(deposit, sources))

    def test_other_deposits_manual_targets_core_links_and_changed_balances_are_never_reallocated(self):
        def another_history(d, s):
            history = json.loads(s["B"].historical_detail)
            history.append(dict(importe_usd=.01, referencia="OTHER"))
            s["B"].historical_detail = json.dumps(history)
        def stale_journal(d, s):
            ledger = json.loads(d.allocation_detail)
            ledger[1]["aplicacion_id"] = "MISSING"
            d.allocation_detail = json.dumps(ledger)
        changes = [another_history, stale_journal,
            lambda d, s: d.update(targets=[dict(historical_application="B")]),
            lambda d, s: d.detail_rows[-1].update(difference_core_item="CORE"),
            lambda d, s: d.detail_rows[-1].update(difference_basis="OUTDATED"),
            lambda d, s: s["B"].update(historical_remitted_usd=40),
            lambda d, s: s["B"].update(application_adjustment_usd=1),
            lambda d, s: d.detail_rows[1].update(client_credit_usd=.01),
            lambda d, s: d.detail_rows[1].update(pending_usd=.02),
            lambda d, s: s["B"].update(historical_period="OTHER")]
        for change in changes:
            with self.subTest(change=change):
                deposit, sources = self.fixture()
                change(deposit, sources)
                saved = copy.deepcopy((deposit, sources))
                with self.assertRaises(repair.ReviewRequired):
                    repair.plan(deposit, sources)
                self.assertEqual((deposit, sources), saved)

    def test_migration_rolls_back_ambiguous_case_and_continues_without_reconciling(self):
        candidates = [Row(parent="BAD"), Row(parent="OK"), Row(parent="OK"), Row(parent="DONE")]
        with patch.object(migration, "_pages", return_value=candidates), \
             patch.object(migration, "repair", side_effect=[repair.ReviewRequired("Compartida"), False, True]) as run, \
             patch.object(frappe, "db", Mock()) as db, patch.object(frappe, "logger", return_value=Mock()):
            result = migration.execute()
        self.assertEqual(result, dict(repaired=["OK"], review={"BAD": "Compartida"}))
        self.assertEqual([call.args[0] for call in run.call_args_list], ["BAD", "DONE", "OK"])
        db.rollback.assert_called_once_with(save_point="repair_company_cent")
        db.commit.assert_not_called()

    def test_unexpected_patch_error_aborts_instead_of_reporting_success(self):
        with patch.object(migration, "_pages", return_value=[Row(parent="BAD")]), \
             patch.object(migration, "repair", side_effect=RuntimeError("DB unavailable")), \
             patch.object(frappe, "db", Mock()), self.assertRaisesRegex(RuntimeError, "DB unavailable"):
            migration.execute()
