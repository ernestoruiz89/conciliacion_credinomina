"""The USD deposit equivalent is derived and never entered by an operator."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import (
    CNRemittanceAllocation,
)


SOURCE_DIR = (
    Path(__file__).resolve().parents[1]
    / "credinomina_reconciliation" / "conciliacion_credinomina"
    / "doctype" / "cn_remittance_allocation"
)


class RemittanceAmountUsdTest(unittest.TestCase):
    def test_equivalent_is_read_only_and_updates_after_submission(self):
        metadata = json.loads(
            (SOURCE_DIR / "cn_remittance_allocation.json").read_text(encoding="utf-8")
        )
        amount = next(
            field for field in metadata["fields"]
            if field["fieldname"] == "amount_usd"
        )
        self.assertEqual(1, amount["read_only"])
        self.assertEqual(1, amount["allow_on_submit"])
        self.assertNotIn("read_only_depends_on", amount)
        self.assertEqual("usd_currency", amount["options"])

    def test_form_recalculates_for_amount_currency_and_rate(self):
        script = (SOURCE_DIR / "cn_remittance_allocation.js").read_text(
            encoding="utf-8"
        )
        for event in ("deposit_amount", "deposit_currency", "fx_rate"):
            self.assertIn(f"{event}: updateUsdEquivalent", script)
        self.assertIn('currency === "USD" ? nativeAmount', script)
        self.assertIn('nativeAmount / rate', script)
        self.assertIn('frm.set_value("amount_usd"', script)

    def test_submitted_rate_change_revalidates_the_derived_amount(self):
        document = SimpleNamespace(
            deposit_date="2026-12-05", deposit_reference=" R-1 ",
            deposit_voucher=" V-1 ", _validate_deposit=Mock(),
        )
        CNRemittanceAllocation.before_update_after_submit(document)
        self.assertEqual("R-1", document.deposit_reference)
        self.assertEqual("V-1", document.deposit_voucher)
        document._validate_deposit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
