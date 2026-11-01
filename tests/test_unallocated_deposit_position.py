import unittest

from credinomina_reconciliation.unallocated_deposit_position import build
from credinomina_reconciliation.client_position import build_position, summary


class UnallocatedDepositTests(unittest.TestCase):
    def test_customer_and_company_portions_do_not_double_count_covered_or_documented_cash(self):
        deposit = dict(name='D', employer='E', deposit_date='2025-04-10', amount_usd=100,
                       allocated_usd=30, justified_surplus_usd=10, unclassified_usd=60)
        details = [dict(name='R', parent='D', client_name='Ana', client_number='1', amount_usd=35, pending_usd=35),
                   dict(name='X', parent='D', row_kind='Diferencia documentada', pending_usd=200)]
        rows = build([deposit], details)
        self.assertEqual([(r['client_name'], r['deposit_pending_usd']) for r in rows],
                         [('Ana', 35), ('Sin cliente individual', 25)])
        all_rows = build_position([], [], [], {}, [], {}, unallocated_deposits=rows)
        self.assertEqual(summary(all_rows)[0]['value'], 60)
        filtered = build_position([], [], [], {}, [], {'client_number': '1'}, unallocated_deposits=rows)
        self.assertEqual(sum(r['deposit_pending_usd'] for r in filtered), 35)

    def test_inconsistent_details_do_not_invent_client_attribution(self):
        deposit = dict(name='D', employer='E', amount_usd=100, allocated_usd=80,
                       justified_surplus_usd=0, unclassified_usd=20)
        details = [dict(name='R', parent='D', client_number='1', amount_usd=50, pending_usd=50)]
        rows = build([deposit], details)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['deposit_pending_usd'], 20)
        self.assertEqual(rows[0]['client_name'], 'Sin cliente individual')
        self.assertIn('Revise', rows[0]['observation'])
        self.assertEqual(build([dict(deposit, cash_identity_status='Origen vinculado')], details), [])
