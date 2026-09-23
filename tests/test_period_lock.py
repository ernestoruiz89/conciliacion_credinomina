import unittest

from credinomina_reconciliation.period_lock import (
    current_period_write_action,
    period_write_action,
)


class PeriodLockTests(unittest.TestCase):
    def test_internal_write_scope_is_temporary_and_nested(self):
        self.assertEqual(current_period_write_action(), "")
        with period_write_action("close"):
            self.assertEqual(current_period_write_action(), "close")
            with period_write_action("reconcile"):
                self.assertEqual(current_period_write_action(), "reconcile")
            self.assertEqual(current_period_write_action(), "close")
        self.assertEqual(current_period_write_action(), "")


if __name__ == "__main__":
    unittest.main()
