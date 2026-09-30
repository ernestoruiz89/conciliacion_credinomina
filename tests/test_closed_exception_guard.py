"""A closed period cannot be changed through its exception records."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_exception import (
    cn_reconciliation_exception as module,
)


class FakeException:
    _assert_related_periods_open = module.CNReconciliationException._assert_related_periods_open
    _refresh_period_exception_count = staticmethod(
        module.CNReconciliationException._refresh_period_exception_count
    )

    def __init__(self, period, previous=None):
        self.period = period
        self.previous = previous
        self.flags = {"skip_comment_reconciliation": True}

    def get_doc_before_save(self):
        return self.previous


class ClosedExceptionGuardTests(unittest.TestCase):
    def _closed_db(self, closed="PER-CLOSED"):
        return SimpleNamespace(
            get_value=lambda doctype, period, field: (
                "Cerrado" if period == closed else "Pendiente"
            ),
            count=Mock(), set_value=Mock(),
        )

    def test_new_exception_cannot_be_inserted_into_closed_period(self):
        exception = FakeException("PER-CLOSED")
        db = self._closed_db()
        with patch.object(module.frappe, "db", db), \
             patch.object(module.frappe, "throw", side_effect=ValueError) as reject:
            with self.assertRaises(ValueError):
                module.CNReconciliationException.validate(exception)
        self.assertIn("Reabrir período", reject.call_args.args[0])
        db.set_value.assert_not_called()

    def test_cannot_move_exception_out_of_closed_period(self):
        exception = FakeException("PER-OPEN", SimpleNamespace(period="PER-CLOSED"))
        with patch.object(module.frappe, "db", self._closed_db()), \
             patch.object(module.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                module.CNReconciliationException.validate(exception)

    def test_on_update_rejects_before_changing_exception_count(self):
        exception = FakeException("PER-CLOSED")
        db = self._closed_db()
        with patch.object(module.frappe, "db", db), \
             patch.object(module.frappe, "throw", side_effect=ValueError):
            with self.assertRaises(ValueError):
                module.CNReconciliationException.on_update(exception)
        db.count.assert_not_called()
        db.set_value.assert_not_called()

    def test_delete_cancel_and_after_submit_update_are_blocked(self):
        exception = FakeException("PER-CLOSED")
        with patch.object(module.frappe, "db", self._closed_db()), \
             patch.object(module.frappe, "throw", side_effect=ValueError):
            for action in (
                module.CNReconciliationException.on_trash,
                module.CNReconciliationException.before_cancel,
                module.CNReconciliationException.before_update_after_submit,
            ):
                with self.subTest(action=action.__name__), self.assertRaises(ValueError):
                    action(exception)

    def test_moving_between_open_periods_refreshes_both_counts(self):
        exception = FakeException("PER-NEW", SimpleNamespace(period="PER-OLD"))
        db = SimpleNamespace(
            get_value=lambda doctype, period, field: (
                "Operativa" if field == "reconciliation_mode" else "Pendiente"
            ),
            count=lambda *_: 2, set_value=Mock(),
        )
        with patch.object(module.frappe, "db", db):
            module.CNReconciliationException.on_update(exception)
        self.assertEqual(db.set_value.call_count, 2)
        self.assertEqual(
            {call.args[1] for call in db.set_value.call_args_list},
            {"PER-NEW", "PER-OLD"},
        )


if __name__ == "__main__":
    unittest.main()
