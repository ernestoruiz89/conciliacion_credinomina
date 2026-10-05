import json
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import deposit_reconciliation as scoped


class SingleDepositTests(unittest.TestCase):
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
        movements = [dict(claim_id="H:H1", consumed_residual_usd=0.01, signed_amount_usd=0.01),
                     dict(claim_id="C:C1", consumed_residual_usd=0, signed_amount_usd=-0.01)]
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
        doc = Mock(docstatus=1)
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
