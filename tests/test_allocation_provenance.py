"""Executed row ownership survives shared H/C claims and signed groups."""
import copy
import unittest

from credinomina_reconciliation.allocation import allocate_cash


class AllocationProvenanceTests(unittest.TestCase):
    def test_each_shared_application_portion_keeps_its_actual_detail_row(self):
        for kind in ('H', 'C'):
            for signed in (False, True):
                with self.subTest(kind=kind, signed=signed):
                    claim = kind + ':APPLICATION'
                    deposits = [dict(id='D', amount_usd=90 if signed else 100)]
                    claims = [dict(id=claim, kind=kind, amount_usd=100)]
                    instructions = [dict(id=row, deposit_id='D', claim_id=claim,
                        amount_usd=amount, detail_row=row) for row, amount in (('R1', 60), ('R2', 40))]
                    if signed:
                        claims.append(dict(id='X:CORE', kind='X', amount_usd=-10))
                        instructions.append(dict(id='NEG', deposit_id='D', claim_id='X:CORE',
                            amount_usd=-10, detail_row='R1'))
                    original = copy.deepcopy((deposits, claims, instructions))
                    result = allocate_cash(deposits, claims, instructions)
                    portions = result['allocations']
                    self.assertEqual([(row['claim_id'], row['amount_usd'], row.get('detail_row'))
                        for row in portions[:2]], [(claim, 60, 'R1'), (claim, 40, 'R2')])
                    if signed:
                        self.assertEqual((portions[2]['amount_usd'], portions[2]['detail_row']), (-10, 'R1'))
                    self.assertEqual(sum(row['amount_usd'] for row in portions), deposits[0]['amount_usd'])
                    self.assertEqual(result['deposit_remaining']['D'], 0)
                    self.assertEqual((deposits, claims, instructions), original)

    def test_allocation_without_row_evidence_does_not_invent_ownership(self):
        result = allocate_cash([dict(id='D', amount_usd=100, reference='REF')],
            [dict(id='H:A', kind='H', amount_usd=100, references=['REF'])])
        self.assertEqual(len(result['allocations']), 1)
        self.assertNotIn('detail_row', result['allocations'][0])
