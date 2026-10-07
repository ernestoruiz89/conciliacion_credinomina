import unittest
from datetime import date

from credinomina_reconciliation.cadence import (
    EXACT_DATE,
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
    def test_additional_exact_dates_for_both_frequencies(self):
        for frequency in (MONTHLY, "Quincenal"):
            self.assertEqual(EXACT_DATE, cycle_for_frequency(frequency, EXACT_DATE))
        month, cut = date(2026, 9, 1), date(2026, 9, 20)
        self.assertEqual(cut, cycle_cutoff(month, EXACT_DATE, cut))
        self.assertEqual("FE", cycle_code(EXACT_DATE))
        for invalid in (None, date(2026, 10, 20), date(2025, 9, 20)):
            with self.assertRaises(ValueError):
                cycle_cutoff(month, EXACT_DATE, invalid)
        self.assertTrue(cycles_conflict(EXACT_DATE, EXACT_DATE, cut, str(cut)))
        self.assertFalse(cycles_conflict(EXACT_DATE, EXACT_DATE, cut, date(2026, 9, 21)))
        for regular in (MONTHLY, FIRST_HALF, SECOND_HALF):
            self.assertFalse(cycles_conflict(regular, EXACT_DATE, cut, cut))
            self.assertFalse(cycles_conflict(EXACT_DATE, regular, cut, cut))

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
