import json
import copy
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import deposit_reconciliation as scoped


class SingleDepositTests(unittest.TestCase):
    def legacy_manual_deposit(self):
        targets = [frappe._dict(name="T" + name, historical_application=name, detail_row="R", amount_usd=amount)
                   for name, amount in (("A", 26), ("B", 23.48))]
        row = frappe._dict(name="R", linked_usd=49.48, matched_targets=json.dumps([
            dict(claim_id="H:" + target.historical_application, amount_usd=target.amount_usd, instruction_id=target.name)
            for target in targets]))
        doc = frappe._dict(name="D", allocated_usd=49.48, targets=targets, detail_rows=[row], allocation_detail=json.dumps([
            dict(tipo="Aplicacion historica", aplicacion_id=target.historical_application, importe_usd=target.amount_usd,
                 origen="Selección manual") for target in targets]))
        ledger = [dict(deposit_id="D", claim_id="H:" + target.historical_application, amount_usd=target.amount_usd,
                       origin="Selección manual", detail_row="R") for target in targets]
        return doc, ledger

    def test_legacy_manual_metadata_completion_preserves_same_applications_and_amounts(self):
        doc, ledger = self.legacy_manual_deposit()
        original = copy.deepcopy(doc)
        with patch.object(frappe, "db", Mock()) as db:
            scoped.assert_preserved_allocations(doc, {"allocations": ledger}, [])
        self.assertEqual(doc, original)
        db.set_value.assert_not_called()
        # A repeat that still lacks the metadata also preserves its old links.
        for entry in ledger:
            entry.pop("detail_row")
        scoped.assert_preserved_allocations(doc, {"allocations": ledger}, [])

    def test_legacy_metadata_proof_does_not_allow_less_cash_another_application_or_row(self):
        for change in (dict(amount_usd=25.99), dict(claim_id="H:OTHER"), dict(detail_row="OTHER")):
            doc, ledger = self.legacy_manual_deposit()
            ledger[0].update(change)
            with self.subTest(change=change), patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
                scoped.assert_preserved_allocations(doc, {"allocations": ledger}, [])

    def test_legacy_partial_manual_target_uses_recorded_cash_not_the_requested_amount(self):
        doc, ledger = self.legacy_manual_deposit()
        doc.targets[0].amount_usd = 30
        scoped.assert_preserved_allocations(doc, {"allocations": ledger}, [])
        # Several stored portions of one confirmed instruction still total 26.
        original = json.loads(doc.allocation_detail)
        original[0]["importe_usd"] = 10
        original.append(dict(original[0], importe_usd=16))
        doc.allocation_detail = json.dumps(original)
        scoped.assert_preserved_allocations(doc, {"allocations": ledger}, [])

    def test_legacy_fifo_or_reclassification_provenance_is_not_treated_as_manual_metadata(self):
        for values in (dict(origen="Automática FIFO"), dict(reclassification_operation_id="OP")):
            doc, ledger = self.legacy_manual_deposit()
            original = json.loads(doc.allocation_detail)
            original[0].update(values)
            doc.allocation_detail = json.dumps(original)
            with self.subTest(values=values), patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
                scoped.assert_preserved_allocations(doc, {"allocations": ledger}, [])

    def test_missing_ambiguous_or_inconsistent_saved_proof_cannot_reassign_legacy_cash(self):
        changes = [lambda d: d.targets[0].update(detail_row="OTHER"),
            lambda d: d.targets[0].update(name="NEW"),
            lambda d: d.targets.append(copy.deepcopy(d.targets[0])),
            lambda d: d.detail_rows.append(copy.deepcopy(d.detail_rows[0])),
            lambda d: d.detail_rows[0].update(matched_targets="broken"),
            lambda d: d.detail_rows[0].update(linked_usd=49),
            lambda d: d.targets[0].update(amount_usd=25)]
        for change in changes:
            doc, ledger = self.legacy_manual_deposit()
            change(doc)
            with self.subTest(change=change), patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
                scoped.assert_preserved_allocations(doc, {"allocations": ledger}, [])

    def test_duplicate_collection_keys_cannot_move_recorded_cash_to_the_last_child(self):
        deposit = frappe._dict(name='D', allocated_usd=100, allocation_detail=json.dumps([
            dict(tipo='Cobranza', periodo='P', fila_id='K', importe_usd=100)]))
        period = frappe._dict(name='P', collection_rows=[
            frappe._dict(name='ORIGINAL', row_key='K'), frappe._dict(name='OTHER', row_key=' K ')])
        with patch.object(frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
            scoped.frozen_cash([deposit], [period], [])

    def test_blank_duplicate_collection_keys_also_require_explicit_review(self):
        period = frappe._dict(name='P', collection_rows=[
            frappe._dict(name='ORIGINAL', row_key=None), frappe._dict(name='OTHER', row_key='')])
        with patch.object(frappe, 'throw', side_effect=ValueError), self.assertRaises(ValueError):
            scoped.frozen_cash([], [period], [])

    def test_repeated_read_of_the_same_child_is_not_a_different_destination(self):
        period = frappe._dict(name='P', collection_rows=[frappe._dict(name='ORIGINAL', row_key='K')])
        deposit = frappe._dict(name='D', allocated_usd=100, allocation_detail=json.dumps([
            dict(tipo='Cobranza', periodo='P', fila_id='K', importe_usd=100)]))
        ledger, coverage = scoped.frozen_cash([deposit], [period, period], [])
        self.assertEqual(ledger[0]['claim_id'], 'C:ORIGINAL')
        self.assertEqual(coverage['C:ORIGINAL'], 100)

    def test_frozen_cash_retains_row_fifo_company_and_reclassification_provenance(self):
        detail = dict(tipo="Cobranza", periodo="P", fila_id="K", importe_usd=30,
            fila_detalle="DETAIL", empresa="E", reclassification_operation_id="OP",
            aplicaciones_fifo=[dict(application_id="A", amount_usd=30)])
        deposit = frappe._dict(name="D", allocated_usd=30, allocation_detail=json.dumps([detail]))
        period = frappe._dict(name="P", collection_rows=[frappe._dict(name="C", row_key="K")])
        ledger, coverage = scoped.frozen_cash([deposit], [period], [])
        self.assertEqual(ledger[0]["detail_row"], "DETAIL")
        self.assertEqual(ledger[0]["fifo_applications"], detail["aplicaciones_fifo"])
        self.assertEqual(ledger[0]["group"], "E")
        self.assertEqual(ledger[0]["reclassification_operation_id"], "OP")
        self.assertEqual(coverage["C:C"], 30)

    def test_normal_reconcile_preserves_links_and_can_increase_or_add_coverage(self):
        document = frappe._dict(name="D", allocated_usd=30, allocation_detail=json.dumps([
            dict(tipo="Aplicacion historica", aplicacion_id="A", importe_usd=30, fila_detalle="R")]))
        for amounts in ([30], [10, 20], [35]):
            ledger = [dict(deposit_id="D", claim_id="H:A", detail_row="R", amount_usd=amount) for amount in amounts]
            ledger.append(dict(deposit_id="D", claim_id="H:B", amount_usd=10))
            scoped.assert_preserved_allocations(document, {"allocations": ledger}, [])
        for ledger in ([], [dict(deposit_id="D", claim_id="H:A", detail_row="R", amount_usd=29)],
                       [dict(deposit_id="D", claim_id="H:B", detail_row="R", amount_usd=30)],
                       [dict(deposit_id="D", claim_id="H:A", detail_row="OTHER", amount_usd=30)]):
            with self.subTest(ledger=ledger), patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
                scoped.assert_preserved_allocations(document, {"allocations": ledger}, [])

    def test_operative_fifo_application_cannot_change_inside_the_same_collection(self):
        document = frappe._dict(name="D", allocated_usd=30, allocation_detail=json.dumps([
            dict(tipo="Cobranza", periodo="P", fila_id="K", importe_usd=30,
                aplicaciones_fifo=[dict(application_id="A", amount_usd=30)])]))
        period = frappe._dict(name="P", collection_rows=[frappe._dict(name="C", row_key="K")])
        ledger = [dict(deposit_id="D", claim_id="C:C", amount_usd=30,
                       fifo_applications=[dict(application_id="B", amount_usd=30)])]
        with patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
            scoped.assert_preserved_allocations(document, {"allocations": ledger}, [period])

    def test_corrupt_stored_ledgers_are_never_silently_ignored(self):
        for raw in ('{}', '[null]', '[{"tipo":"Other","importe_usd":1}]',
                    '[{"tipo":"Aplicacion historica","aplicacion_id":"A","importe_usd":-1}]',
                    '[{"tipo":"Aplicacion historica","aplicacion_id":"A","importe_usd":"NaN"}]'):
            with self.subTest(raw=raw), patch.object(frappe, "throw", side_effect=ValueError), self.assertRaises(ValueError):
                scoped.frozen_cash([frappe._dict(name="D", allocation_detail=raw)], [], [])

    def test_stored_cash_context_preserves_split_origins_and_signed_items(self):
        raw = json.dumps([
            dict(tipo="Aplicacion historica", aplicacion_id="A", importe_usd=30, origen="Automática FIFO"),
            dict(tipo="Aplicacion historica", aplicacion_id="A", importe_usd=20, origen="Automática FIFO"),
            dict(tipo="Partida complementaria", partida="X", importe_usd=-0.01, origen="Selección manual"),
        ])
        doc = frappe._dict(name="D", employer="EMP", deposit_date="2025-05-01", deposit_reference="REF",
            deposit_currency="NIO", deposit_amount=1831.581243, amount_usd=50.01, fx_rate=36.6243,
            allocated_usd=50, unallocated_usd=0.01, unclassified_usd=0, justified_surplus_usd=0.01,
            allocation_detail=raw)
        movement = frappe._dict(name="TOL", period="P", claim_id="H:A", deposit_source_row="D",
            application_source_row="A", signed_amount_usd=0.01, absorbed_cash_usd=0.01)
        with patch.object(scoped.frappe, "get_all", return_value=[movement]):
            result = scoped.stored_cash_context([doc], [])
        self.assertEqual([entry["amount_usd"] for entry in result["allocations"]], [30, 20, -0.01])
        self.assertEqual([entry["origin"] for entry in result["allocations"]],
                         ["Automática FIFO", "Automática FIFO", "Selección manual"])
        self.assertEqual(result["coverage"]["H:A"], 50)
        self.assertEqual(result["rounding_movements"][0]["name"], "TOL")
        self.assertEqual(result["deposit_meta"]["D"]["account"].allocation_detail, raw)
        self.assertEqual(result["deposit_meta"]["D"]["account"].justified_surplus_usd, 0.01)
        self.assertEqual(result["registered_ids"], {"D": "D"})

    def test_existing_cash_and_both_tolerance_signs_reserve_capacity(self):
        period = frappe._dict(name="P", collection_rows=[frappe._dict(name="C1", row_key="K")])
        deposit = frappe._dict(name="D", allocation_detail=json.dumps([
            {"tipo": "Cobranza", "periodo": "P", "fila_id": "K", "importe_usd": 40},
            {"tipo": "Aplicacion historica", "aplicacion_id": "H1", "importe_usd": 60},
            {"tipo": "Partida complementaria", "partida": "X1", "importe_usd": -10},
            {"tipo": "Movimiento de conciliación", "importe_usd": 0.01},
        ]))
        movements = [dict(deposit_id="D", claim_id="H:H1", consumed_residual_usd=0.01, signed_amount_usd=0.01),
                     dict(deposit_id="D", claim_id="C:C1", consumed_residual_usd=0, signed_amount_usd=-0.01)]
        allocations, coverage = scoped.frozen_cash([deposit], [period], movements)
        self.assertEqual(len(allocations), 3)
        self.assertEqual(float(coverage["C:C1"]), 40.01)
        self.assertEqual(float(coverage["H:H1"]), 60)
        self.assertEqual(float(coverage["X:X1"]), -10)

    def test_affected_periods_include_removed_and_new_destinations(self):
        periods = [frappe._dict(name="P", collection_rows=[frappe._dict(name="C1", row_key="K")])]
        deposit = frappe._dict(allocation_detail='[{"periodo":"OLD"}]', detail_periods=[{"period": "DETAIL"}],
                              targets=[frappe._dict(period="MANUAL")])
        allocation = dict(allocations=[dict(claim_id="C:C1"), dict(claim_id="H:H1"), dict(claim_id="X:X1")],
                          rounding_movements=[dict(period="RND")])
        sources = [frappe._dict(name="H1", event_type="Aplicacion", historical_period="HIST")]
        items = [frappe._dict(name="X1", period="FEE")]
        self.assertEqual(scoped.affected_periods(deposit, allocation, periods, sources, items),
                         {"OLD", "DETAIL", "MANUAL", "P", "HIST", "FEE", "RND"})

    def test_endpoint_selects_individual_deposit_engine(self):
        from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as api
        doc = Mock(docstatus=1, apply_fifo=False, detail_rows=[])
        with patch.object(api.frappe, "get_doc", return_value=doc), \
             patch.object(scoped, "reconcile_deposit", return_value={"scope": "deposit"}) as run:
            self.assertEqual(api.reconcile_remittance("D"), {"scope": "deposit"})
        run.assert_called_once()
        self.assertIs(run.call_args.args[0], doc)
        doc._reconcile.assert_not_called()

    def test_incomplete_stored_cash_evidence_cannot_be_dropped(self):
        deposit = frappe._dict(name="D", allocated_usd=100, allocation_detail="invalid")
        with patch.object(scoped.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                scoped.frozen_cash([deposit], [], [])


if __name__ == "__main__":
    unittest.main()
