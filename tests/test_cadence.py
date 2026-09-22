import unittest
from datetime import date

from credinomina_reconciliation.cadence import (
    FIRST_HALF,
    MONTHLY,
    SECOND_HALF,
    cycle_code,
    cycle_cutoff,
    cycle_for_frequency,
    cycles_conflict,
    unique_full_quincena_pair,
)


class CollectionCadenceTest(unittest.TestCase):
    def test_monthly_and_fortnightly_cycle_selection(self):
        self.assertEqual(MONTHLY, cycle_for_frequency("Mensual", ""))
        self.assertEqual(FIRST_HALF, cycle_for_frequency("Quincenal", FIRST_HALF))
        self.assertEqual(SECOND_HALF, cycle_for_frequency("Quincenal", SECOND_HALF))
        with self.assertRaises(ValueError):
            cycle_for_frequency("Quincenal", "")
        with self.assertRaises(ValueError):
            cycle_for_frequency("Mensual", FIRST_HALF)

    def test_cutoffs_include_month_end_and_leap_year(self):
        month = date(2028, 2, 1)
        self.assertEqual(date(2028, 2, 15), cycle_cutoff(month, FIRST_HALF))
        self.assertEqual(date(2028, 2, 29), cycle_cutoff(month, SECOND_HALF))
        self.assertEqual(date(2028, 2, 29), cycle_cutoff(month, MONTHLY))
        self.assertEqual("Q1", cycle_code(FIRST_HALF))
        self.assertEqual("Q2", cycle_code(SECOND_HALF))

    def test_month_can_have_two_quincenas_but_not_monthly_and_quincenal(self):
        self.assertFalse(cycles_conflict(FIRST_HALF, SECOND_HALF))
        self.assertTrue(cycles_conflict(FIRST_HALF, FIRST_HALF))
        self.assertTrue(cycles_conflict(MONTHLY, FIRST_HALF))
        self.assertTrue(cycles_conflict("", SECOND_HALF))

    def test_one_core_application_can_cover_both_quincenas(self):
        first = {"cycle": FIRST_HALF, "employer": "A", "month": "2026-09", "available_usd": 40}
        second = {"cycle": SECOND_HALF, "employer": "A", "month": "2026-09", "available_usd": 60}
        self.assertEqual([first, second], unique_full_quincena_pair([first, second], 100))
        self.assertEqual([], unique_full_quincena_pair([first, second], 90))

    def test_grouped_application_must_be_unique_within_employer_and_month(self):
        first = {"cycle": FIRST_HALF, "employer": "A", "month": "2026-09", "available_usd": 40}
        second = {"cycle": SECOND_HALF, "employer": "A", "month": "2026-10", "available_usd": 60}
        self.assertEqual([], unique_full_quincena_pair([first, second], 100))
        second["month"] = "2026-09"
        duplicate = {**second, "available_usd": 60}
        self.assertEqual([], unique_full_quincena_pair([first, second, duplicate], 100))


if __name__ == "__main__":
    unittest.main()
