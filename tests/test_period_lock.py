import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as controller

from credinomina_reconciliation.period_lock import (
    current_period_write_action,
    period_write_action,
)


class PeriodLockTests(unittest.TestCase):
    def test_closed_period_rejects_normal_saves_and_direct_status_changes(self):
        def reject(message):
            raise ValueError(message)
        previous = SimpleNamespace(status="Cerrado")
        for status in ("Cerrado", "Borrador", "Historico pendiente"):
            doc = SimpleNamespace(status=status, remark="Cambio no permitido",
                get_doc_before_save=Mock(return_value=previous),
                flags={"allow_closed": True, "period_write_action": "reopen"})
            with patch.object(controller, "_", side_effect=lambda text: text), \
                 patch.object(controller.frappe, "throw", side_effect=reject):
                with self.assertRaisesRegex(ValueError, "Use Reabrir período"):
                    controller.CNReconciliationPeriod._validate_closed_transition(doc)

    def test_controlled_reopen_still_allows_editing(self):
        doc = SimpleNamespace(status="Historico conciliado",
            get_doc_before_save=Mock(return_value=SimpleNamespace(status="Cerrado")))
        with period_write_action("reopen"), patch.object(controller.frappe, "throw") as throw:
            controller.CNReconciliationPeriod._validate_closed_transition(doc)
            throw.assert_not_called()

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
