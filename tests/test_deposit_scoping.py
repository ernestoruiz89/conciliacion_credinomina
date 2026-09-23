import unittest

from credinomina_reconciliation.deposit_scoping import (
    resolved_deposit_employer,
)


class DepositScopingTest(unittest.TestCase):
    def test_unknown_or_conflicting_employer_cannot_auto_allocate(self):
        aliases = {"acme": "ACME", "beta": "BETA"}
        self.assertEqual(
            ("ACME", False),
            resolved_deposit_employer(
                {"employer_text": ""}, {"employer_text": "ACME"}, aliases, set()
            ),
        )
        self.assertEqual(
            (None, True),
            resolved_deposit_employer(
                {"employer_text": ""}, {"employer_text": "DESCONOCIDA"},
                aliases, set(),
            ),
        )
        self.assertEqual(
            (None, True),
            resolved_deposit_employer(
                {"employer_text": "ACME"}, {"employer_text": "BETA"},
                aliases, set(),
            ),
        )


if __name__ == "__main__":
    unittest.main()
