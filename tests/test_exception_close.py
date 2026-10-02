"""Open exceptions must prevent both historical and operative period closure."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import (
    cn_reconciliation_period as period_module,
)


class ExceptionCloseTests(unittest.TestCase):
    def test_close_reconciles_and_reloads_before_accepting_historical_status(self):
        period = SimpleNamespace(
            name="HIST-2025-04-15", employer="EMP-1", status="Conciliado",
            reconciliation_mode="Historica", check_permission=Mock(),
        )
        period.reload = Mock(side_effect=lambda: setattr(
            period, "status", "Parcial"
        ))
        with patch.object(period_module.frappe, "get_doc", return_value=period), \
             patch.object(period_module.frappe, "db", SimpleNamespace(count=lambda *_: 0)), \
             patch.object(period_module.frappe, "get_all", return_value=["APP-1"]), \
             patch.object(period_module.frappe, "throw", side_effect=ValueError) as reject, \
             patch.object(period_module, "_reconcile_if_sources", return_value={"updated": 1}) as reconcile, \
             patch.object(period_module, "_pending_remittance_details_for_period", return_value=[]), \
             patch.object(period_module, "_mark_period_closed") as mark_closed:
            with self.assertRaises(ValueError):
                period_module.close_period(period.name)

        reconcile.assert_called_once()
        self.assertEqual(reconcile.call_args.args, ("EMP-1",))
        self.assertTrue(callable(reconcile.call_args.kwargs["progress"]))
        period.reload.assert_called_once()
        self.assertIn("cubierto por depósitos o compensado por ajustes", reject.call_args.args[0])
        mark_closed.assert_not_called()

    def test_historical_period_cannot_close_with_open_exception(self):
        period = SimpleNamespace(
            name="HIST-2025-04-15", status="Conciliado",
            reconciliation_mode="Historica", employer="EMP-1", check_permission=Mock(),
        )

        def count(doctype, _filters):
            return 1 if doctype == "CN Reconciliation Exception" else 0

        with patch.object(period_module.frappe, "get_doc", return_value=period), \
             patch.object(period_module.frappe, "db", SimpleNamespace(count=count)), \
             patch.object(period_module.frappe, "throw", side_effect=ValueError) as reject, \
             patch.object(period_module, "_reconcile_if_sources", return_value=None), \
             patch.object(period_module, "_pending_remittance_details_for_period", return_value=[]), \
             patch.object(period_module, "_mark_period_closed") as mark_closed:
            with self.assertRaises(ValueError):
                period_module.close_period(period.name)

        reject.assert_called_once()
        mark_closed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
