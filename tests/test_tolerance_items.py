import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from credinomina_reconciliation import tolerance_items as module
from credinomina_reconciliation.patches.v1_0.integrate_reconciliation_movements import complementary_values


class ToleranceItemTests(unittest.TestCase):
    def setUp(self):
        for target, name, kwargs in ((module, "_", {"side_effect": lambda s: s}),
                                      (module.frappe, "throw", {"side_effect": ValueError})):
            p = patch.object(target, name, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def document(self, **kwargs):
        return SimpleNamespace(**(dict(category=module.CATEGORY, movement_key="K", period="P", employer="E",
            claim_id="H:A", signed_amount_usd=0.01, tolerance_usd=0.01, voucher="", reason="Tolerancia") | kwargs))

    def test_manual_creation_edit_and_category_change_are_blocked(self):
        for doc, previous in ((self.document(), None),
                              (self.document(category="Otros ingresos", movement_key=""), self.document()),
                              (self.document(category="Otros ingresos"), None)):
            with self.assertRaises(ValueError):
                module.guard_tolerance_item(doc, previous)

    def test_trusted_write_context_does_not_escape_or_leak(self):
        with self.assertRaises(RuntimeError):
            with module.tolerance_item_write():
                self.assertTrue(module.guard_tolerance_item(self.document()))
                raise RuntimeError("rollback")
        with self.assertRaises(ValueError):
            module.guard_tolerance_item(self.document())

    def test_automatic_amounts_are_signed_internal_not_pending_accounting(self):
        for amount in (0.01, -0.01):
            doc = self.document(signed_amount_usd=amount)
            module.validate_tolerance_item(doc)
            self.assertEqual(doc.amount_usd, amount)
            self.assertEqual(doc.amount, amount)
            self.assertEqual(doc.accounting_status, "No requiere registro")
            self.assertEqual(doc.currency, "USD")

    def test_invalid_adjustments_and_voucher_are_rejected(self):
        for overrides in (dict(signed_amount_usd=0), dict(signed_amount_usd=0.02),
                          dict(tolerance_usd=0), dict(voucher="AS1"), dict(period=None)):
            with self.assertRaises(ValueError):
                module.validate_tolerance_item(self.document(**overrides))

    def test_migration_keeps_identifiers_reversal_and_financial_audit(self):
        for state in ("Vigente", "Revertido"):
            old = frappe._dict(name="CN-RND-X", movement_key="CN-RND-X", status=state,
                signed_amount_usd=-0.01, absorbed_cash_usd=0, tolerance_usd=0.01,
                core_applied_usd=46.53, deposit_usd=46.52, claim_usd=46.53,
                deposit_reference="REF", deposit_date="2025-05-10", period="P", employer="E",
                reversed_on="2026-09-30 12:00:00" if state == "Revertido" else None,
                reversal_reason="Cambio de vínculo" if state == "Revertido" else None,
                reason="Solo conciliación", creation="2026-09-01 10:00:00")
            new = complementary_values(old)
            self.assertEqual(new["category"], module.CATEGORY)
            self.assertEqual(new["docstatus"], 1)
            self.assertEqual(new["accounting_status"], "No requiere registro")
            self.assertEqual(new["name"], old.name)
            self.assertEqual(new["status"], old.status)
            self.assertEqual(new["reversed_on"], old.reversed_on)
            self.assertEqual(new["amount_usd"], -0.01)
            self.assertEqual(new["absorbed_cash_usd"], 0)
            self.assertEqual(new["creation"], old.creation)

    def test_schema_uses_existing_category_not_a_separate_origin(self):
        root = Path(__file__).resolve().parents[1] / "credinomina_reconciliation/conciliacion_credinomina"
        schema = json.loads((root / "doctype/cn_complementary_item/cn_complementary_item.json").read_text())
        fields = {f["fieldname"]: f for f in schema["fields"]}
        self.assertIn(module.CATEGORY, fields["category"]["options"].splitlines())
        self.assertIn("No requiere registro", fields["accounting_status"]["options"].splitlines())
        self.assertTrue(fields["movement_key"]["unique"])
        self.assertTrue(fields["status"]["allow_on_submit"])
        self.assertNotIn("origin", fields)
        self.assertEqual(set(schema["field_order"]), set(fields))
        workspace = json.loads((root / "workspace/conciliacion_credinomina/conciliacion_credinomina.json").read_text())
        self.assertNotIn("CN Reconciliation Movement", json.dumps(workspace))
