import copy
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import deposit_difference as difference
from credinomina_reconciliation.allocation import allocate_cash
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


class DepositDifferenceTests(unittest.TestCase):
    def setup_case(self, delta=-.03, core=False):
        self.doc = frappe._dict(name="DEP", employer="EMP", amount_usd=100 + delta, fx_rate=0,
            detail_file="detail.csv", detail_hash="HASH", detail_source_file="detail.csv", apply_fifo=0)
        self.rows = [frappe._dict(name=f"R{i}", parent="DEP", employer="EMP", client_number=str(i),
                                 deducted_usd=amount, amount_usd=amount) for i, amount in enumerate((60, 40))]
        self.diff = frappe._dict(name="DIFF", parent="DEP", row_kind=difference.KIND,
            difference_amount_usd=delta, difference_reason="Inmaterial", difference_notes="Soporte verificado",
            difference_core_item="CORE" if core else "")
        self.rows.append(self.diff)
        self.diff.difference_basis = difference.basis(self.doc, self.rows)
        self.claims = [dict(id=f"H:{i}", kind="H", amount_usd=amount, group="EMP", client_number=str(i))
                       for i, amount in enumerate((60, 40))]
        if core:
            self.claims.append(dict(id="X:CORE", kind="X", amount_usd=delta, group="EMP", accounting_source_key="KEY"))

    def reconcile(self, manual=()):
        deposits = [dict(id="DEP", amount_usd=self.doc.amount_usd, group="EMP", currency="USD", bank_currency="USD")]
        with patch.object(frappe, "get_all", return_value=self.rows), \
                patch("credinomina_reconciliation.client_credit.load_credits", return_value=[]):
            context = engine._prepare_remittance_details([self.doc], {"DEP": "DEP"}, deposits, self.claims, list(manual), {})
        result = allocate_cash(deposits, self.claims, [*manual, *context["instructions"]], context["blocked_deposits"])
        with patch.object(frappe, "db", Mock()) as db, patch(
                "credinomina_reconciliation.remittance_target_summary.load_target_descriptions", return_value={}):
            statuses = engine._sync_remittance_details(context, result, self.claims)
            updates = {call.args[1]: call.args[2] for call in db.set_value.call_args_list if call.args[0] == "CN Remittance Detail"}
        return result, statuses, updates

    def test_negative_documented_difference_never_invents_cash_or_erases_claims(self):
        self.setup_case()
        self.claims[1]['amount_usd'] = 39
        before = copy.deepcopy(self.rows)
        result, statuses, rows = self.reconcile()
        self.assertEqual(statuses["DEP"], 'Revisar filas')
        self.assertEqual([r["amount_usd"] for r in result["allocations"]], [60])
        self.assertEqual(result["deposit_remaining"]["DEP"], 39.97)
        self.assertEqual(result["claim_remaining"], {"H:0": 0, "H:1": 39})
        self.assertEqual(rows["DIFF"]["difference_core_pending_usd"], .03)
        self.assertEqual(rows["R0"]["pending_usd"], 0)
        self.assertEqual(rows["R0"]["match_status"], "Conciliada")
        self.assertEqual(rows["R1"]["pending_usd"], 40)
        self.assertEqual(before, self.rows)
        self.assertEqual(result, self.reconcile()[0])

    def test_positive_difference_keeps_unassigned_cash_until_core_exists(self):
        self.setup_case(.03)
        result, statuses, rows = self.reconcile()
        self.assertEqual(statuses["DEP"], difference.SETTLED)
        self.assertEqual(result["deposit_remaining"]["DEP"], .03)
        self.assertEqual(sum(r["amount_usd"] for r in result["allocations"]), 100)
        self.assertEqual(rows["DIFF"]["difference_core_pending_usd"], .03)

    def test_future_core_settles_same_row_for_both_signs_without_double_count(self):
        for delta in (-.03, .03):
            with self.subTest(delta=delta):
                self.setup_case(delta, core=True)
                result, statuses, rows = self.reconcile()
                self.assertEqual(statuses["DEP"], "Conciliado")
                self.assertEqual(result["deposit_remaining"]["DEP"], 0)
                self.assertTrue(all(value == 0 for value in result["claim_remaining"].values()))
                self.assertEqual(rows["DIFF"]["difference_core_pending_usd"], 0)
                self.assertEqual(rows["DIFF"]["difference_core_status"], "Conciliada contra core")
                self.assertEqual(sum(r["claim_id"] == "X:CORE" for r in result["allocations"]), 1)
                self.assertEqual(result, self.reconcile()[0])

    def test_stale_or_duplicate_documentation_does_not_unblock_detail(self):
        for change in ("amount", "file", "duplicate", "reason"):
            self.setup_case()
            if change == "amount": self.rows[0].deducted_usd = 60.01
            if change == "file": self.doc.detail_hash = "NEW"
            if change == "duplicate": self.rows.append(copy.deepcopy(self.diff))
            if change == "reason": self.diff.difference_reason = ""
            result, statuses, rows = self.reconcile()
            self.assertEqual(result["allocations"], [], change)
            self.assertNotEqual(statuses["DEP"], difference.SETTLED)
            self.assertEqual(rows["DIFF"]["match_status"], "Revisar")

    def test_invalid_or_consumed_core_never_releases_documentary_cash(self):
        for change in ({"amount_usd": .03}, {"amount_usd": -.02}, {"accounting_source_key": ""},
                       {"group": "OTHER"}, {"period_closed": True}):
            self.setup_case(core=True)
            self.claims[-1].update(change)
            result, statuses, _ = self.reconcile()
            self.assertFalse(result["allocations"])
            self.assertNotEqual(statuses["DEP"], "Conciliado")

    def test_reason_alone_cannot_bypass_excess_and_manual_targets_are_not_scaled(self):
        self.setup_case()
        self.rows.remove(self.diff)
        self.doc.difference_reason = "Inmaterial"
        self.assertFalse(self.reconcile()[0]["allocations"])
        self.rows.append(self.diff)
        state = difference.prepare(self.doc, self.rows, self.claims, [{"id": "MANUAL"}])
        self.assertIn("destinos manuales", state["error"])

    def test_schema_guard_prevents_manual_company_rows_and_preserves_existing_ones(self):
        self.setup_case()
        current = frappe._dict(detail_rows=self.rows)
        old = frappe._dict(detail_rows=self.rows[:-1])
        with patch.object(frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError): difference.guard_rows(current, old)
            difference.guard_rows(current, copy.deepcopy(current))
            modified = copy.deepcopy(current)
            modified.detail_rows[-1].difference_core_item = "FORGED"
            with self.assertRaises(ValueError): difference.guard_rows(modified, current)

    def test_insufficient_cash_blocks_before_allocating_any_customer(self):
        self.setup_case()
        with self.assertRaisesRegex(frappe.ValidationError, 'no se repartirá'):
            self.reconcile()

    def test_difference_preserves_manual_split_and_prior_period_destinations(self):
        self.setup_case()
        self.claims[1]['amount_usd'] = 39
        self.doc.allow_manual_other_periods = 1
        self.claims[0:1] = [dict(id="H:OLD", kind="H", amount_usd=10, group="EMP", client_number="0", period="OLD"),
                            dict(id="H:SECOND", kind="H", amount_usd=50, group="EMP", client_number="0", period="SECOND")]
        manual = [dict(id="M1", deposit_id="DEP", claim_id="H:OLD", amount_usd=10, detail_row="R0", origin="Manual"),
                  dict(id="M2", deposit_id="DEP", claim_id="H:SECOND", amount_usd=50, detail_row="R0", origin="Manual")]
        original = copy.deepcopy(manual)
        result, statuses, rows = self.reconcile(manual)
        self.assertEqual(statuses["DEP"], 'Revisar filas')
        self.assertEqual(manual, original)
        self.assertEqual(result["instruction_results"]["M1"], "Aplicada")
        self.assertEqual(result["instruction_results"]["M2"], "Aplicada")
        self.assertEqual(rows["R0"]["pending_usd"], 0)
        self.assertEqual(rows["R0"]["match_status"], "Conciliada")
        self.assertEqual(rows["R1"]["pending_usd"], 40)
        self.assertEqual(rows["R1"]["match_status"], "Revisar")
        self.assertEqual(result["deposit_remaining"]["DEP"], 39.97)
        self.assertEqual(sum(r["amount_usd"] for r in result["allocations"]), 60)

    def test_shortage_cannot_reduce_explicit_manual_amounts(self):
        self.setup_case()
        manual=[dict(id='M',deposit_id='DEP',claim_id='H:0',amount_usd=60,detail_row='R0',origin='Manual')]
        before=copy.deepcopy(manual)
        with self.assertRaisesRegex(frappe.ValidationError, 'no se repartirá'):
            self.reconcile(manual)
        self.assertEqual(manual,before)

    def test_difference_preserves_positive_imported_complement_link(self):
        self.setup_case()
        self.claims[1]['amount_usd'] = 39
        self.claims[0].update(id='X:IMPORTED',kind='X',accounting_source_key='CORE')
        manual=[dict(id='MX',deposit_id='DEP',claim_id='X:IMPORTED',amount_usd=60,detail_row='R0',origin='Manual')]
        result,statuses,rows=self.reconcile(manual)
        self.assertEqual(statuses['DEP'],'Revisar filas')
        self.assertEqual(result['instruction_results']['MX'],'Aplicada')
        self.assertEqual(rows['R0']['pending_usd'],0)
        self.assertEqual(rows['R1']['pending_usd'],40)
        self.claims[0]['accounting_source_key']=''
        self.assertTrue(difference.prepare(self.doc,self.rows,self.claims,manual)['error'])

    def test_documentary_row_checks_periods_without_canceling_client_credits(self):
        doc = Mock(docstatus=1, modified="STAMP", employer="EMP", targets=[])
        doc.detail_rows = []
        doc.append.return_value = frappe._dict(name="NEW")
        doc.get.return_value = None
        with patch.object(frappe, "get_doc", return_value=doc), patch.object(frappe, "db", Mock()), \
                patch("credinomina_reconciliation.deposit_reconciliation.lock_cash_pool"), \
                patch("credinomina_reconciliation.paying_employers.reconciliation_companies", return_value={"EMP"}), \
                patch.object(difference, "detail_difference", return_value=difference.money('.03')), \
                patch.object(difference, "basis", return_value="HASH"):
            difference.record("DEP", "STAMP", "Inmaterial", "Soporte")
        doc._assert_open_related_periods.assert_called_once()
        doc._assert_can_reverse_distribution.assert_not_called()
        doc.save.assert_called_once()

    def test_manual_only_row_is_not_automatically_reassigned(self):
        self.setup_case(delta=0)
        self.rows.remove(self.diff)
        self.rows[0].manual_reconciliation_required=1
        result, statuses, rows=self.reconcile()
        self.assertEqual([a['claim_id'] for a in result['allocations']],['H:1'])
        self.assertEqual(rows['R0']['match_status'],'Pendiente manual')
        self.assertEqual(result['deposit_remaining']['DEP'],60)
        manual=[dict(id='M1',deposit_id='DEP',claim_id='H:0',amount_usd=60,detail_row='R0',origin='Manual')]
        result, statuses, rows=self.reconcile(manual)
        self.assertEqual(result['deposit_remaining']['DEP'],0)
        self.assertEqual(rows['R0']['match_status'],'Conciliada')

    def test_cent_rows_with_no_cash_still_require_identifiable_applications(self):
        self.setup_case(-.02)
        self.doc.amount_usd = .01
        for row, claim, amount in zip(self.rows, self.claims, (.01, .02)):
            row.deducted_usd = row.amount_usd = claim["amount_usd"] = amount
        self.diff.difference_basis = difference.basis(self.doc, self.rows)
        with self.assertRaisesRegex(frappe.ValidationError, 'no se repartirá'):
            self.reconcile()

    def test_rpc_requires_write_permission_before_locking_or_writing(self):
        with patch.object(frappe, "get_doc") as get_doc, \
                patch("credinomina_reconciliation.deposit_reconciliation.lock_cash_pool") as lock:
            get_doc.return_value.check_permission.side_effect = frappe.PermissionError
            with self.assertRaises(frappe.PermissionError):
                difference.record("DEP", "STAMP", "Inmaterial", "Soporte")
            get_doc.return_value.check_permission.assert_called_once_with("write")
            lock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
