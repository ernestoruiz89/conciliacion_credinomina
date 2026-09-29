"""The registered deposit and its reconciliation are separate user actions."""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCTYPE = ROOT / "credinomina_reconciliation" / "conciliacion_credinomina" / "doctype"


class RemittanceActionSeparationTests(unittest.TestCase):
    def test_form_has_distinct_confirmation_and_reconciliation_buttons(self):
        script = (DOCTYPE / "cn_remittance_allocation" / "cn_remittance_allocation.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('__("Confirmar depósito")', script)
        self.assertIn('__("Conciliar")', script)
        self.assertNotIn("Confirmar depósito y conciliar", script)
        self.assertIn(".reconcile_remittance", script)

    def test_confirmation_and_post_submit_edits_do_not_run_reconciliation_hooks(self):
        source = (DOCTYPE / "cn_remittance_allocation" / "cn_remittance_allocation.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        allocation = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "CNRemittanceAllocation"
        )
        methods = {node.name for node in allocation.body if isinstance(node, ast.FunctionDef)}
        self.assertNotIn("on_submit", methods)
        self.assertNotIn("on_update_after_submit", methods)
        self.assertIn("on_cancel", methods)
        self.assertIn(
            "reconcile_remittance",
            {node.name for node in tree.body if isinstance(node, ast.FunctionDef)},
        )


if __name__ == "__main__":
    unittest.main()
