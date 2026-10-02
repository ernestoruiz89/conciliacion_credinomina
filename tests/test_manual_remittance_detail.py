"""Explicit row links resolve ambiguity without duplicating the cash ledger."""
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.allocation import allocate_cash
from credinomina_reconciliation.remittance_detail import manual_detail_targets
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as source


class ManualRemittanceDetailTests(unittest.TestCase):
    def setUp(self):
        self.row = frappe._dict(name="ROW", parent="DEP", client_number="3538",
                               client_name="JULIO", deducted_nio=824.78, deducted_usd=0)
        self.claims = [dict(id="H:" + name, kind="H", amount_usd=22.52,
                            group="EMP", client_number="3538", period="APRIL")
                       for name in ("A", "B")]
        self.manual = [dict(id="TARGET", deposit_id="DEP", claim_id="H:A",
                            amount_usd=22.52, detail_row="ROW")]
        self.deposits = [dict(id="DEP", amount_usd=22.52, group="EMP", currency="USD", bank_currency="NIO")]

    def reconcile(self, manual=None, descriptions=None):
        manual = self.manual if manual is None else manual
        remittance = frappe._dict(name="DEP", employer="EMP", detail_file="detail.xlsx",
                                 detail_hash="hash", detail_source_file="detail.xlsx", fx_rate=36.6243)
        with patch.object(source.frappe, "get_all", return_value=[self.row]):
            context = source._prepare_remittance_details(
                [remittance], {"DEP": "DEP"}, self.deposits, self.claims, manual, {},
            )
        result = allocate_cash(self.deposits, self.claims, manual + context["instructions"],
                               context["blocked_deposits"])
        with patch.object(source.frappe, "db", Mock()) as db, patch(
            "credinomina_reconciliation.remittance_target_summary.load_target_descriptions",
            return_value=descriptions or {"H:A": {"loan_number": "108331-1"}, "H:B": {"loan_number": "108331-1"}},
        ):
            statuses = source._sync_remittance_details(context, result, self.claims)
            saved_row = next(call.args[2] for call in db.set_value.call_args_list
                             if call.args[0] == "CN Remittance Detail")
        return context, result, statuses, saved_row

    def test_manual_nio_link_resolves_two_possible_applications_once(self):
        self.assertEqual(self.reconcile([])[2]["DEP"], "Revisar filas")
        context, result, statuses, row = self.reconcile()
        self.assertEqual(context["instructions"], [])
        self.assertEqual(len(result["allocations"]), 1)
        self.assertEqual(result["allocations"][0]["amount_usd"], 22.52)
        self.assertEqual(result["deposit_remaining"]["DEP"], 0)
        self.assertEqual(statuses["DEP"], "Conciliado")
        self.assertEqual(row["match_status"], "Conciliada")
        self.assertIn("manual", row["match_reason"])
        self.assertIn('"H:A"', row["matched_targets"])
        self.assertEqual(row["loan_number"], "108331-1")
        # Re-running reconstructs the same allocation rather than doubling it.
        self.assertEqual(self.reconcile()[1]["allocations"], result["allocations"])

    def test_invalid_link_never_closes_detail(self):
        for field, value in (("client_number", "OTHER"), ("group", "OTHER")):
            with self.subTest(field=field):
                original = self.claims[0][field]
                self.claims[0][field] = value
                self.assertEqual(self.reconcile()[2]["DEP"], "Revisar filas")
                self.claims[0][field] = original
        self.manual[0]["amount_usd"] = 20
        self.assertEqual(self.reconcile()[2]["DEP"], "Revisar filas")
        self.row.deducted_nio = 0
        self.assertEqual(self.reconcile()[2]["DEP"], "Revisar filas")

    def test_fully_covered_detail_still_requires_successful_allocation(self):
        self.claims[0]["amount_usd"] = 10
        _, result, statuses, row = self.reconcile()
        self.assertEqual(result["instruction_results"]["TARGET"], "Excede cobranza")
        self.assertEqual(statuses["DEP"], "Revisar filas")
        self.assertEqual(row["match_status"], "Revisar")

    def test_one_detail_row_can_cover_several_manual_applications(self):
        self.manual[0]["amount_usd"] = 10
        self.manual.append(dict(id="TARGET-2", deposit_id="DEP", claim_id="H:B",
                                amount_usd=12.52, detail_row="ROW"))
        self.assertEqual(self.reconcile()[2]["DEP"], "Conciliado")

    def test_multiple_target_credits_do_not_guess_a_single_loan_number(self):
        self.manual[0]["amount_usd"] = 10
        self.manual.append(dict(id="TARGET-2", deposit_id="DEP", claim_id="H:B",
                                amount_usd=12.52, detail_row="ROW"))
        _, _, _, saved_row = self.reconcile(descriptions={
            "H:A": {"loan_number": "108331-1"},
            "H:B": {"loan_number": "109136-1"},
        })
        self.assertNotIn("loan_number", saved_row)

    def test_manual_link_respects_period_scope(self):
        targets, reason = manual_detail_targets(self.row, self.claims, self.manual, 22.52, "EMP", "MAY")
        self.assertEqual(targets, [])
        self.assertIn("período", reason)

    def test_periodless_identified_complement_is_valid_only_when_explicitly_linked(self):
        claims = [dict(id="H:A", kind="H", group="EMP", period="APRIL", client_number="3538", amount_usd=63.73),
                  dict(id="X:ITEM", kind="X", group="EMP", period="", client_number="3538", amount_usd=63.74)]
        manual = [dict(id="CORE", claim_id="H:A", amount_usd=63.73),
                  dict(id="COMP", claim_id="X:ITEM", amount_usd=63.74)]
        targets, _ = manual_detail_targets(self.row, claims, manual, 127.47, "EMP", ["APRIL", "MAY"])
        self.assertEqual(len(targets), 2)
        for invalid in ({"period": "OTHER"}, {"client_number": "OTHER"}, {"group": "OTHER"}):
            bad = [claims[0], claims[1] | invalid]
            self.assertFalse(manual_detail_targets(self.row, bad, manual, 127.47, "EMP", ["APRIL"])[0])

    def test_shared_generic_complement_covers_multiple_clients_without_double_booking(self):
        rows = [frappe._dict(name="R1", parent="DEP", employer="EMP", client_name="ANA", client_number="1", loan_number="1-1", deducted_usd=200),
                frappe._dict(name="R2", parent="DEP", employer="EMP", client_name="LUIS", client_number="2", loan_number="2-1", deducted_usd=300)]
        claim = dict(id="X:GENERIC", kind="X", manual_only=True, group="EMP", groups=["EMP"], amount_usd=500)
        manual = [dict(id="M1", deposit_id="DEP", claim_id=claim["id"], amount_usd=200, detail_row="R1"),
                  dict(id="M2", deposit_id="DEP", claim_id=claim["id"], amount_usd=300, detail_row="R2")]
        remittance = frappe._dict(name="DEP", employer="EMP", detail_file="detail.xlsx", detail_hash="hash", detail_source_file="detail.xlsx",
                                 detail_periods=[frappe._dict(period="APRIL")])
        deposits = [dict(id="DEP", group="EMP", amount_usd=500)]
        with patch.object(source.frappe, "get_all", return_value=rows):
            context = source._prepare_remittance_details([remittance], {"DEP": "DEP"}, deposits, [claim], manual, {})
        self.assertEqual(context["instructions"], [])
        allocation = allocate_cash(deposits, [claim], manual, context["blocked_deposits"])
        with patch.object(source.frappe, "db", Mock()), patch("credinomina_reconciliation.remittance_target_summary.load_target_descriptions", return_value={}):
            self.assertEqual(source._sync_remittance_details(context, allocation, [claim]), {"DEP": "Conciliado"})
        self.assertEqual(allocation["claim_remaining"][claim["id"]], 0)
        self.assertEqual(sum(entry["amount_usd"] for entry in allocation["allocations"]), 500)
        self.assertEqual([entry["detail_row"] for entry in allocation["allocations"]], ["R1", "R2"])

    def test_separate_negative_complement_explains_detail_above_cash_received(self):
        self.row.deducted_nio = 0
        self.row.deducted_usd = 100
        self.claims = [dict(id="H:A", kind="H", amount_usd=100, group="EMP", client_number="3538"),
                       dict(id="X:SHORT", kind="X", amount_usd=-10, group="EMP")]
        self.manual = [dict(id="SHORT", deposit_id="DEP", claim_id="X:SHORT", amount_usd=-10)]
        self.deposits[0]["amount_usd"] = 90
        _, result, statuses, _ = self.reconcile()
        self.assertEqual(statuses["DEP"], "Conciliado")
        self.assertEqual(sum(entry["amount_usd"] for entry in result["allocations"]), 90)
        self.assertEqual(result["deposit_remaining"]["DEP"], 0)

    def administrative_collection(self, extra=16.84):
        self.row.deducted_nio = 0
        self.row.deducted_usd = 103.16
        self.claims = [dict(id="H:A", kind="H", amount_usd=103.16, group="EMP", client_number="3538"),
                       dict(id="X:ADMIN", kind="X", amount_usd=16.84, group="EMP")]
        self.manual = [dict(id="ADMIN", deposit_id="DEP", claim_id="X:ADMIN", amount_usd=extra)]
        self.deposits[0]["amount_usd"] = 120

    def test_administrative_collection_outside_client_detail_covers_deposit(self):
        self.administrative_collection()
        context, result, statuses, row = self.reconcile()
        self.assertEqual(statuses["DEP"], "Conciliado")
        self.assertEqual(result["instruction_results"]["ADMIN"], "Aplicada")
        self.assertEqual(result["deposit_remaining"]["DEP"], 0)
        self.assertEqual(context["contexts"]["DEP"]["total_usd"], 103.16)
        self.assertEqual(context["contexts"]["DEP"]["covered_total_usd"], 120)
        self.assertEqual(len(context["contexts"]["DEP"]["rows"]), 1)
        self.assertEqual(row["amount_usd"], 103.16)
        self.assertNotIn("X:ADMIN", row["matched_targets"])
        self.assertEqual(self.reconcile()[1]["allocations"], result["allocations"])
        # Removing the destination restores the unexplained amount.
        self.assertEqual(self.reconcile([])[2]["DEP"], "Parcial; saldo sin detalle")

    def test_positive_complement_must_really_be_applied(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                self.administrative_collection()
                if missing:  # Unconfirmed/cancelled items do not enter claims.
                    self.claims.pop()
                else:  # Insufficient remaining capacity.
                    self.claims[1]["amount_usd"] = 10
                _, result, statuses, _ = self.reconcile()
                self.assertNotEqual(result["instruction_results"]["ADMIN"], "Aplicada")
                self.assertEqual(statuses["DEP"], "Revisar filas")
                self.assertEqual(result["deposit_remaining"]["DEP"], 16.84)

    def test_partial_complement_does_not_hide_even_one_cent(self):
        for extra, remaining in ((6.84, 10), (16.83, 0.01)):
            with self.subTest(extra=extra):
                self.administrative_collection(extra)
                _, result, statuses, _ = self.reconcile()
                self.assertEqual(statuses["DEP"], "Parcial; saldo sin detalle")
                self.assertEqual(result["deposit_remaining"]["DEP"], remaining)

    def test_detail_plus_complement_cannot_exceed_deposit(self):
        self.administrative_collection(20)
        self.claims[1]["amount_usd"] = 20
        self.assertEqual(self.reconcile()[2]["DEP"], "Detalle supera depósito")

    def test_linked_complement_is_not_counted_twice(self):
        self.administrative_collection()
        self.row.deducted_usd = 120
        self.claims[1]["client_number"] = "3538"
        self.manual[0]["detail_row"] = "ROW"
        self.manual.append(dict(id="LOAN", deposit_id="DEP", claim_id="H:A",
                                amount_usd=103.16, detail_row="ROW"))
        context, _, statuses, _ = self.reconcile()
        self.assertEqual(context["contexts"]["DEP"]["outside_complements"], [])
        self.assertEqual(statuses["DEP"], "Conciliado")

    def test_same_rule_applies_to_operative_collection(self):
        self.administrative_collection()
        self.claims[0].update(id="C:A", kind="C")
        self.assertEqual(self.reconcile()[2]["DEP"], "Conciliado")
