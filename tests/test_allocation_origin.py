import json
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.allocation import allocate_cash
from credinomina_reconciliation.allocation_origin import DETAIL, MANUAL, REFERENCE, TOLERANCE, UNRECORDED
from credinomina_reconciliation.deposit_reconciliation import frozen_cash
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


class AllocationOriginTests(unittest.TestCase):
    def test_explicit_instructions_preserve_their_origin_without_affecting_money(self):
        deposits = [dict(id="D", amount_usd=90)]
        claims = [dict(id="H:A", amount_usd=100), dict(id="X:C", kind="X", amount_usd=-10)]
        instructions = [dict(id="T1", deposit_id="D", claim_id="H:A", amount_usd=100, origin=DETAIL),
                        dict(id="T2", deposit_id="D", claim_id="X:C", amount_usd=-10)]
        result = allocate_cash(deposits, claims, instructions)
        self.assertEqual([r['origin'] for r in result['allocations']], [DETAIL, MANUAL])
        self.assertEqual(result['deposit_remaining'], {'D': 0})
        self.assertEqual(result['claim_remaining'], {'H:A': 0, 'X:C': 0})

    def test_all_reference_matching_paths_report_automatic_reference(self):
        cases = [
            (100, [dict(id='A', amount_usd=100)]),
            (100, [dict(id='A', amount_usd=40), dict(id='B', amount_usd=60)]),
            (50, [dict(id='A', amount_usd=40, hints={'REF': 20}),
                  dict(id='B', amount_usd=60, hints={'REF': 30})]),
        ]
        for amount, claims in cases:
            with self.subTest(amount=amount, claims=len(claims)):
                claims = [dict(references=['REF'], **c) for c in claims]
                result = allocate_cash([dict(id='D', reference='REF', amount_usd=amount)], claims)
                self.assertTrue(result['allocations'])
                self.assertTrue(all(r['origin'] == REFERENCE for r in result['allocations']))
                self.assertEqual(result['deposit_remaining']['D'], 0)

    def test_frozen_deposits_keep_evidence_instead_of_guessing_manual(self):
        for origin in (DETAIL, REFERENCE, MANUAL, 'Manual', None):
            with self.subTest(origin=origin):
                d = frappe._dict(name='D', allocated_usd=10, allocation_detail=json.dumps([
                    dict(tipo='Aplicacion historica', aplicacion_id='A', importe_usd=10, origen=origin)]))
                rows, _ = frozen_cash([d], [], [])
                self.assertEqual(rows[0]['origin'], origin or UNRECORDED)

    def test_historical_detail_persists_origin_for_the_excel_and_tolerance(self):
        period = frappe._dict(name='P', reconciliation_mode='Historica', status='Pendiente')
        row = frappe._dict(name='A', event_type='Aplicacion', effective=1, historical_period='P',
                          match_status='Conciliado', amount=46.52)
        account = frappe._dict(reference='REF', voucher='V', event_date='2025-05-10')
        allocation = dict(allocations=[dict(claim_id='H:A', deposit_id='D', amount_usd=46.52, origin=DETAIL)],
                          deposit_meta={'D': {'account': account}}, rounding_movements=[
                              dict(claim_id='H:A', deposit_id='D', name='ADJ', signed_amount_usd=.01,
                                   consumed_residual_usd=.01, period='P')])
        with patch.object(engine, '_save_reconciled_document'):
            engine._rebuild_historical_balances([period], [row], allocation)
        details = json.loads(row.historical_detail)
        self.assertEqual([r['origen'] for r in details], [DETAIL, TOLERANCE])
