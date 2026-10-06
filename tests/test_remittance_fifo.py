import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.allocation import allocate_cash
from credinomina_reconciliation.allocation_origin import FIFO, MANUAL
from credinomina_reconciliation.remittance_detail import suggest_detail_targets, detail_amount_usd
from credinomina_reconciliation.remittance_target_summary import describe_targets
from credinomina_reconciliation.remittance_selection import pending_selection
from credinomina_reconciliation.rounding import money, sum_money
from credinomina_reconciliation.parsers import parse_collection_file, normalize_credit_number
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def application(name, day, amount, **extra):
    return dict(id="H:" + name, kind="H", group="EMP", period="P1", client="5464",
        client_number="5464", loan_number="109578-1", amount_usd=amount,
        core_applied_usd=amount, fifo_applications=[dict(id=name, event_date=day, amount_usd=amount)], **extra)


class FifoMatchingTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(client="5464", client_number="5464", loan_number="109578-1")
        self.claims = [application("NEW", "2025-04-30", 60), application("OLD", "2025-04-15", 40)]

    def suggest(self, amount=70, **kwargs):
        return suggest_detail_targets(self.row, self.claims, amount, "EMP", kwargs.pop("period", ["P1"]),
                                      apply_fifo=kwargs.pop("apply_fifo", True), **kwargs)

    def test_opt_in_splits_partial_by_application_date(self):
        self.assertEqual(self.suggest(apply_fifo=False)[0], [])
        targets, reason = self.suggest()
        self.assertEqual([(t["claim_id"], t["amount_usd"]) for t in targets], [("H:OLD", 40), ("H:NEW", 30)])
        self.assertIn("FIFO", reason)
        self.assertIn("2025-04-15", describe_targets(targets, {}, date_formatter=str))

    def test_only_selected_periods_and_companies(self):
        self.claims.append(application("ELSE", "2025-01-01", 100))
        self.claims[-1]["period"] = "ELSE"
        self.assertEqual(len(self.suggest()[0]), 2)
        self.claims[-1].update(period="P1", group="OTHER")
        self.assertEqual(len(self.suggest()[0]), 2)
        self.assertEqual(self.suggest(period=[])[0], [])

    def test_identity_conflicts_and_multiple_loans_are_not_guessed(self):
        self.row["client_number"] = "OTHER"
        self.assertEqual(self.suggest()[0], [])
        self.row = dict(client_number="5464")
        self.claims[0]["loan_number"] = "OTHER-1"
        self.assertEqual(self.suggest()[0], [])
        self.claims[0]["loan_number"] = "109578-1"
        self.assertEqual(len(self.suggest()[0]), 2)  # A unique credit can be inferred.

    def test_missing_date_closed_period_and_complements_are_not_fifo_capacity(self):
        self.claims[0]["fifo_applications"][0]["event_date"] = ""
        self.assertEqual(self.suggest()[0], [])
        self.claims[0]["period_closed"] = True
        self.assertEqual(self.suggest()[0], [])
        self.claims[0].update(period_closed=False, kind="X")
        self.assertEqual(self.suggest()[0], [])

    def test_date_ties_are_stable_and_independent_of_input_order(self):
        self.claims[0]["fifo_applications"][0]["event_date"] = "2025-04-15"
        first = self.suggest()[0]
        self.claims.reverse()
        self.assertEqual(self.suggest()[0], first)

    def test_other_deposits_adjustments_and_manual_reservations_reduce_capacity(self):
        self.claims[1].update(amount_usd=25, core_applied_usd=25, fifo_covered_usd=15)
        targets, _ = self.suggest(70, reserved_amounts={"H:OLD": money(10)})
        self.assertEqual([t["amount_usd"] for t in targets], [15, 55])
        self.assertEqual(self.suggest(75.01, reserved_amounts={"H:OLD": money(10)})[0], [])

    def test_operative_slices_use_actual_dates_not_period_order(self):
        self.claims[0].update(kind="C", id="C:ROW", amount_usd=200, core_applied_usd=60,
            fifo_applications=[dict(id="A", event_date="2025-04-01", amount_usd=20),
                               dict(id="B", event_date="2025-04-30", amount_usd=40)])
        targets, _ = self.suggest(70)
        self.assertEqual([(t["claim_id"], t["amount_usd"]) for t in targets], [("C:ROW", 30), ("H:OLD", 40)])
        self.assertEqual([a["amount_usd"] for a in targets[0]["fifo_applications"]], [20, 10])
        self.assertEqual(self.suggest(100.01)[0], [])  # Deduction is not an application.

    def test_operative_fixed_cash_is_consumed_before_new_slices(self):
        self.claims = [dict(id="C:R", kind="C", group="EMP", period="P1", client="5464",
            client_number="5464", loan_number="109578-1", amount_usd=55, core_applied_usd=55,
            fifo_covered_usd=15, fifo_applications=[dict(id="A", event_date="2025-04-01", amount_usd=20),
                dict(id="B", event_date="2025-04-30", amount_usd=50)])]
        targets, _ = self.suggest(40, reserved_amounts={"C:R": money(10)})
        self.assertEqual(targets[0]["fifo_applications"], [dict(application_id="B", event_date="2025-04-30", amount_usd=40)])


class FifoDetailEngineTests(unittest.TestCase):
    def setUp(self):
        self.rows = [frappe._dict(name="ROW1", parent="DEP", client="5464", client_number="5464",
            loan_number="109578-1", deducted_usd=33.03),
            frappe._dict(name="ROW2", parent="DEP", client="5464", client_number="5464",
            loan_number="109578-1", deducted_usd=22.02)]
        self.claims = [application("A", "2025-04-15", 40), application("B", "2025-04-30", 60)]
        self.deposit = dict(id="DEP", group="EMP", amount_usd=55.05, currency="USD", bank_currency="USD")
        self.doc = frappe._dict(name="DEP", employer="EMP", apply_fifo=1,
            detail_periods=[frappe._dict(period="P1")], detail_file="file.xlsx", detail_hash="hash",
            detail_source_file="file.xlsx")

    def run_engine(self, manual=()):
        with patch.object(engine.frappe, "get_all", return_value=copy.deepcopy(self.rows)), \
                patch("credinomina_reconciliation.client_credit.load_credits", return_value=[]):
            context = engine._prepare_remittance_details([self.doc], {"DEP": "DEP"},
                [self.deposit], self.claims, list(manual), {})
        result = allocate_cash([self.deposit], self.claims, list(manual) + context["instructions"], context["blocked_deposits"])
        return context, result

    def test_repeated_client_rows_share_remaining_capacity_without_double_booking(self):
        context, result = self.run_engine()
        self.assertEqual([(t["claim_id"], t["amount_usd"]) for t in context["instructions"]],
                         [("H:A", 33.03), ("H:A", 6.97), ("H:B", 15.05)])
        self.assertEqual(result["deposit_remaining"]["DEP"], 0)
        self.assertEqual(result["claim_remaining"], {"H:A": 0, "H:B": 44.95})
        self.assertTrue(all(t["origin"] == FIFO for t in result["allocations"]))
        self.assertEqual([t["detail_row"] for t in result["allocations"]], ["ROW1", "ROW2", "ROW2"])
        self.assertEqual(self.run_engine()[1], result)  # Reconciliation is repeatable.

    def test_identical_rows_are_not_discarded(self):
        self.rows[1].deducted_usd = 33.03
        self.deposit["amount_usd"] = 66.06
        _, result = self.run_engine()
        self.assertEqual(sum_money(t["amount_usd"] for t in result["allocations"]), money(66.06))

    def test_manual_row_has_priority_even_when_it_is_last_in_file(self):
        manual = [dict(id="MANUAL", deposit_id="DEP", claim_id="H:A", amount_usd=22.02,
                       detail_row="ROW2", origin=MANUAL)]
        context, result = self.run_engine(manual)
        self.assertEqual([t["amount_usd"] for t in context["instructions"]], [17.98, 15.05])
        self.assertEqual(result["instruction_results"]["MANUAL"], "Aplicada")
        self.assertEqual(result["deposit_remaining"]["DEP"], 0)

    def test_picker_can_link_fifo_remainder_to_another_detail_row(self):
        # Two detail rows of 18.67 cover applications of 18.66 and 18.67.
        # FIFO uses one cent of the second application in the first detail row.
        for row in self.rows:
            row.deducted_usd = 18.67
        self.claims = [application("A", "2025-04-15", 18.66), application("B", "2025-04-30", 18.67)]
        self.deposit["amount_usd"] = 37.34
        _, initial = self.run_engine()
        self.assertEqual(initial["claim_remaining"]["H:B"], 18.66)
        selection = pending_selection(
            [{"historical_application": "B", "applied_usd": 18.67, "due_usd": 18.67}],
            [{"name": "DEP", "docstatus": 1, "allocation_detail": [
                {"aplicacion_id": entry["claim_id"][2:], "importe_usd": entry["amount_usd"],
                 "origen": entry["origin"]} for entry in initial["allocations"]
            ]}], "DEP", 37.34, [],
        )
        self.assertEqual(selection["rows"][0]["pending_cents"], 1866)
        manual = [dict(id="MANUAL", deposit_id="DEP", claim_id="H:B", amount_usd=18.66,
                       detail_row="ROW2", origin=MANUAL)]
        context, result = self.run_engine(manual)
        self.assertEqual(result["instruction_results"]["MANUAL"], "Aplicada")
        self.assertEqual(result["claim_remaining"], {"H:A": 0, "H:B": 0})
        self.assertEqual(result["deposit_remaining"]["DEP"], 0.01)
        self.assertEqual(sum_money(entry["amount_usd"] for entry in result["allocations"]), money(37.33))
        self.assertEqual([(entry["claim_id"], entry["amount_usd"]) for entry in context["instructions"]],
                         [("H:A", 18.66), ("H:B", 0.01)])
        with patch.object(engine.frappe, "db", Mock()) as db, patch(
            "credinomina_reconciliation.remittance_target_summary.load_target_descriptions", return_value={},
        ):
            db.get_single_value.return_value = "dd-MM-yyyy"
            engine._sync_remittance_details(context, result, self.claims)
        saved = {call.args[1]: call.args[2] for call in db.set_value.call_args_list
                 if call.args[0] == "CN Remittance Detail"}
        self.assertEqual(saved["ROW1"]["match_status"], "Conciliada")
        self.assertEqual(saved["ROW2"]["linked_usd"], 18.66)
        self.assertEqual(saved["ROW2"]["pending_usd"], 0.01)
        self.assertEqual(saved["ROW2"]["match_status"], "Revisar")
        self.assertEqual(self.run_engine(manual)[1], result)

    def test_one_cent_excess_is_not_silently_written_off(self):
        self.deposit["amount_usd"] = 55.04
        context, result = self.run_engine()
        self.assertEqual(context["instructions"], [])
        self.assertEqual(context["contexts"]["DEP"]["status"], "Detalle supera depósito")
        self.assertEqual(result["allocations"], [])

    def test_repeated_rows_round_half_up_individually(self):
        for row, amount in zip(self.rows, ("31.355", "18.67")):
            row.deducted_usd = amount
        self.deposit["amount_usd"] = 50.03
        context, result = self.run_engine()
        self.assertEqual(context["contexts"]["DEP"]["total_usd"], 50.03)
        self.assertEqual(detail_amount_usd(self.rows[0])[0], 31.36)
        self.assertEqual(result["deposit_remaining"]["DEP"], 0)

    def test_twenty_eight_rows_keep_repetitions_and_explicit_rounding_difference(self):
        # Synthetic identities with the submitted example's amounts; do not
        # store real borrowers' names or identifiers in regression fixtures.
        amounts = ("33.03 31.355 36.29 30.395 31.645 34.825 40.05 28.025 31.475 39.15 20.62 28.26 172.02 50.16 "
                   "22.02 18.67 28.54 16.75 19.25 25.61 36.06 12.01 18.91 39.15 20.62 28.26 172.02 50.16").split()
        content = "Nombre y Apellidos del Cliente,Nro. Crédito,Deducido US$\n" + "\n".join(
            f"Cliente {i % 14},{1001 + i % 14},{amount}" for i, amount in enumerate(amounts))
        parsed = parse_collection_file("detail.csv", content.encode(), require_deduction=True, require_identity=True)
        self.assertEqual(len(parsed), 28)
        self.rows = []
        self.claims = []
        for i, record in enumerate(parsed):
            identity = str(i % 14)
            record.update(name=f"ROW{i}", parent="DEP", client=identity, client_number=identity,
                          loan_number=normalize_credit_number(record["loan_number"]))
            self.rows.append(frappe._dict(record))
            if i < 14:
                claim = application(f"A{i}", "2025-04-15", 500)
                claim.update(client=identity, client_number=identity, loan_number=record["loan_number"])
                self.claims.append(claim)
        self.deposit["amount_usd"] = 1115.36
        context, result = self.run_engine()
        self.assertEqual(context["contexts"]["DEP"]["total_usd"], 1115.36)
        self.assertEqual(len(result["allocations"]), 28)
        self.assertEqual(result["deposit_remaining"]["DEP"], 0)
        self.deposit["amount_usd"] = 1115.33
        context, result = self.run_engine()
        self.assertEqual(context["contexts"]["DEP"]["status"], "Detalle supera depósito")
        self.assertEqual(result["allocations"], [])

    def test_fifo_does_not_enable_automatic_tolerance_adjustments(self):
        self.rows = self.rows[:1]
        self.rows[0].deducted_usd = 39.99
        self.deposit["amount_usd"] = 39.99
        context, result = self.run_engine()
        self.assertEqual(context["rounding_eligible"], set())
        self.assertEqual(result["claim_remaining"]["H:A"], 0.01)

    def test_flag_is_optional_and_editable_on_confirmed_deposits(self):
        path = Path(__file__).parents[1] / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation/cn_remittance_allocation.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        field = next(f for f in schema["fields"] if f["fieldname"] == "apply_fifo")
        self.assertEqual(field["default"], "0")
        self.assertEqual(field["allow_on_submit"], 1)

    def test_server_rejects_fifo_without_selected_periods(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as api
        doc = frappe._dict(employer="EMP", deposit_date="2025-05-09", deposit_amount=55.05,
                           docstatus=0, detail_rows=[], detail_periods=[], apply_fifo=1)
        with patch.object(api, "allowed_employers", return_value={"EMP"}), \
                patch.object(api.frappe, "throw", side_effect=ValueError) as error:
            with self.assertRaises(ValueError):
                api.CNRemittanceAllocation._validate_deposit(doc)
        self.assertIn("al menos un período", error.call_args.args[0])
