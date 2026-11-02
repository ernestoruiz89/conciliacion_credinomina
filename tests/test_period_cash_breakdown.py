import unittest

from credinomina_reconciliation.period_cash_breakdown import calculate


def portion(claim, amount, deposit='D'):
    return dict(deposit_id=deposit, claim_id=claim, amount_usd=amount)


class PeriodCashBreakdownTests(unittest.TestCase):
    def test_single_application_keeps_the_negative_sign_and_actual_cash(self):
        result = calculate('P', [portion('H:A', 100), portion('X:N', -10)], {'H:A': 'P'})
        self.assertTrue(result['known'])
        self.assertEqual(result['negative_complementary_usd'], -10)
        self.assertEqual(100 + result['cash_adjustment_usd'], 90)

    def test_several_applications_in_one_period_have_an_exact_period_total_only(self):
        ledger = [portion('H:A', 60), portion('H:B', 40), portion('X:N', -10)]
        result = calculate('P', ledger, {'H:A': 'P', 'H:B': 'P'})
        self.assertEqual(result['cash_adjustment_usd'], -10)
        self.assertNotIn('applications', result)
        self.assertEqual(ledger, [portion('H:A', 60), portion('H:B', 40), portion('X:N', -10)])

    def test_shared_periods_never_receive_an_inferred_split(self):
        ledger = [portion('H:A', 60), portion('H:B', 40), portion('X:N', -10)]
        for period in ('P1', 'P2'):
            with self.subTest(period=period):
                result = calculate(period, ledger, {'H:A': 'P1', 'H:B': 'P2'})
                self.assertFalse(result['known'])
                self.assertIsNone(result['cash_adjustment_usd'])
                self.assertIsNone(result['negative_complementary_usd'])
        self.assertEqual(calculate('UNRELATED', ledger, {'H:A': 'P1', 'H:B': 'P2'})['cash_adjustment_usd'], 0)

    def test_positive_complement_or_other_destination_makes_effect_on_loans_unknown(self):
        for other in ('X:FEE', 'T:MOVEMENT'):
            with self.subTest(other=other):
                result = calculate('P', [portion('H:A', 100), portion(other, 5), portion('X:N', -10)], {'H:A': 'P'})
                self.assertFalse(result['known'])
                self.assertIn('otros destinos', result['reason'])

    def test_missing_application_period_cannot_be_assumed_to_be_the_requested_one(self):
        result = calculate('P', [portion('H:A', 100), portion('X:N', -10)], {})
        self.assertFalse(result['known'])
        self.assertIn('Falta identificar', result['reason'])

    def test_normal_distributions_keep_existing_totals_and_other_periods_are_excluded(self):
        self.assertEqual(calculate('P', [portion('H:A', 90), portion('X:FEE', 10)], {'H:A': 'P'})['cash_adjustment_usd'], 0)
        self.assertEqual(calculate('P', [portion('H:B', 100), portion('X:N', -10)], {'H:B': 'OTHER'})['cash_adjustment_usd'], 0)
        self.assertEqual(calculate('P', [portion('X:FEE', 100), portion('X:N', -10)], {})['cash_adjustment_usd'], 0)

    def test_many_deposits_are_counted_once_with_cent_precision(self):
        ledger = [portion('H:A', 50), portion('X:N', -0.01),
                  portion('C:ROW', 25, 'D2'), portion('X:N2', -0.02, 'D2')]
        result = calculate('P', ledger, {'H:A': 'P', 'C:ROW': 'P'})
        self.assertEqual(result['cash_adjustment_usd'], -0.03)
        self.assertEqual(result['negative_complementary_usd'], -0.03)

    def test_invalid_net_or_nonfinite_amount_never_produces_a_known_cash_total(self):
        for ledger in ([portion('H:A', 5), portion('X:N', -10)],
                       [portion('H:A', 100), portion('X:N', '-Infinity')],
                       [portion('H:A', -10)]):
            with self.subTest(ledger=ledger):
                self.assertFalse(calculate('P', ledger, {'H:A': 'P'})['known'])
