"""Exercise the correction action without requiring a Frappe installation."""
import ast
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation.rounding import money, decimal_value, MONEY_EPSILON


ROOT = Path(__file__).resolve().parents[1]
DOCTYPE = ROOT / "credinomina_reconciliation/conciliacion_credinomina/doctype/cn_remittance_allocation"


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.doc = Mock(docstatus=1, modified="current", name="DEP-1", deposit_amount=100,
                        deposit_currency="USD", allocated_usd=0, justified_surplus_usd=0, fx_rate=0)
        self.doc.flags = SimpleNamespace()
        self.doc.get.side_effect = {"employer": "OLD", "deposit_date": "2026-10-01",
                                    "deposit_reference": "REF"}.get
        self.frappe = ModuleType("frappe")
        self.frappe.get_doc = Mock()
        self.frappe.throw = Mock(side_effect=ValueError)
        utils = ModuleType("frappe.utils")
        utils.getdate = date.fromisoformat
        credit = ModuleType("credinomina_reconciliation.client_credit")
        credit.lock_credit_deposit = Mock(return_value=self.doc)
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils,
            "credinomina_reconciliation.client_credit": credit})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        tree = ast.parse((DOCTYPE / "cn_remittance_allocation.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "correct_deposit_data")
        function.decorator_list = []
        namespace = {"frappe": self.frappe, "_": lambda text: text,
                     "clean_text": lambda text: str(text or "").strip(),
                     "money": money, "decimal_value": decimal_value, "MONEY_EPSILON": MONEY_EPSILON}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "correction", "exec"), namespace)
        self.correct = namespace["correct_deposit_data"]

    def call(self, **changes):
        args = dict(remittance_name="DEP-1", modified="current", employer="NEW",
                    deposit_date="2026-10-02", deposit_reference=" NEW REF ", reason=" Error ")
        return self.correct(**(args | changes))

    def test_validated_save_and_audit(self):
        self.call()
        self.doc.check_permission.assert_called_once_with("write")
        self.doc.update.assert_called_once_with({"employer": "NEW", "deposit_date": date(2026, 10, 2),
                                                "deposit_reference": "NEW REF", "deposit_amount": money(100)})
        self.assertTrue(self.doc.flags.ignore_validate_update_after_submit)
        self.doc.save.assert_called_once_with()
        self.assertIn("Motivo: Error", self.doc.add_comment.call_args.args[1])

    def test_rejects_draft_cancelled_stale_and_missing_values(self):
        for changes, status in [({}, 0), ({}, 2), ({"modified": "stale"}, 1),
                                ({"reason": " "}, 1), ({"deposit_reference": ""}, 1)]:
            self.doc.docstatus = status
            with self.subTest(changes=changes, status=status), self.assertRaises(ValueError):
                self.call(**changes)
        self.doc.save.assert_not_called()

    def test_save_validation_failure_does_not_record_success(self):
        self.doc.save.side_effect = ValueError("closed period or incompatible employer")
        with self.assertRaises(ValueError):
            self.call()
        self.doc.add_comment.assert_not_called()

    def test_correction_never_reduces_executed_or_reserved_cash(self):
        self.doc.allocated_usd, self.doc.justified_surplus_usd = 80, 10
        with self.assertRaises(ValueError):
            self.call(deposit_amount=89.99)
        self.doc.save.assert_not_called()
        self.doc.add_comment.assert_not_called()

    def test_corrected_native_amount_is_compared_in_usd(self):
        self.doc.deposit_currency, self.doc.fx_rate = 'NIO', 36.6243
        self.doc.deposit_amount, self.doc.allocated_usd = 3662.43, 90
        with self.assertRaises(ValueError):
            self.call(deposit_amount=1831.22)
        self.doc.save.assert_not_called()

    def test_fields_remain_locked_for_regular_post_submit_edits(self):
        meta = json.loads((DOCTYPE / "cn_remittance_allocation.json").read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in meta["fields"]}
        for field in ("employer", "deposit_date", "deposit_reference"):
            self.assertFalse(fields[field].get("allow_on_submit"))


if __name__ == "__main__":
    unittest.main()
