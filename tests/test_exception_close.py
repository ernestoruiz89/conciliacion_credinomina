"""Open exceptions must prevent both historical and operative period closure."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import (
    cn_reconciliation_period as period_module,
)


class ExceptionCloseTests(unittest.TestCase):
    def test_historical_period_cannot_close_with_open_exception(self):
        period = SimpleNamespace(
            name="HIST-2025-04-15", status="Historico conciliado",
            reconciliation_mode="Historica", check_permission=Mock(),
        )

        def count(doctype, _filters):
            return 1 if doctype == "CN Reconciliation Exception" else 0

        with patch.object(period_module.frappe, "get_doc", return_value=period), \
             patch.object(period_module.frappe, "db", SimpleNamespace(count=count)), \
             patch.object(period_module.frappe, "throw", side_effect=ValueError) as reject, \
             patch.object(period_module, "_mark_period_closed") as mark_closed:
            with self.assertRaises(ValueError):
                period_module.close_period(period.name)

        reject.assert_called_once()
        mark_closed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
