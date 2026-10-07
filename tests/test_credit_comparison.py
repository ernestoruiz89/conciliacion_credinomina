import re
import unittest

from credinomina_reconciliation.parsers import canonical_credit_number, canonical_identifier, credit_number_pattern
from credinomina_reconciliation.reconciliation import application_matches_collection, match_collection_record


class CreditComparisonTests(unittest.TestCase):
    def test_numeric_padding_is_ignored_without_touching_cycle_or_other_identifiers(self):
        for number in (1807, "1807-1", "001807-1", "00001807", "'001807"):
            self.assertEqual(canonical_credit_number(number), "1807-1")
        self.assertEqual(canonical_credit_number("0000-1"), "0-1")
        self.assertNotEqual(canonical_credit_number("001807-2"), "1807-1")
        self.assertEqual(canonical_credit_number("00ABC-1"), "00abc-1")
        self.assertEqual(canonical_identifier("001-234"), "001-234")
        self.assertEqual(canonical_credit_number(None), "")

    def test_database_filter_uses_same_equivalence_and_escapes_non_numeric_loans(self):
        pattern = credit_number_pattern(["001807-1", "00011-2", "A+B.1"])
        for value in ("1807", "001807", "00001807-1", "11-2", "00011-2", "a+b.1"):
            self.assertIsNotNone(re.fullmatch(pattern, value))
        for value in ("11807-1", "1807-2", "11", "00011-1", "abX1", ""):
            self.assertIsNone(re.fullmatch(pattern, value))

    def test_first_reconciliation_and_returned_row_key_compare_normalized_loans(self):
        collection = {"loan_number": "001807-1", "client_number": "7", "row_key": "ROW"}
        application = {"loan_number": "1807-1", "client_number": "7"}
        self.assertTrue(application_matches_collection(application, collection))
        self.assertEqual(match_collection_record({**application, "row_key": "ROW"}, [collection])[0], collection)
        for change in ({"loan_number": "1807-2"}, {"client_number": "8"}):
            self.assertFalse(application_matches_collection({**application, **change}, collection))
            self.assertIsNone(match_collection_record({**application, **change, "row_key": "ROW"}, [collection])[0])
