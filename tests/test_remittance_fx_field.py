"""A remittance rate is usable without a written source justification."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation.patches.v1_0.move_remittance_fx_evidence_to_notes import (
    execute as move_old_evidence,
)
from credinomina_reconciliation.reconciliation import remittance_fx_basis


ROOT = Path(__file__).resolve().parents[1]


class RemittanceFxFieldTest(unittest.TestCase):
    def test_fx_evidence_field_is_removed(self):
        path = (
            ROOT / "credinomina_reconciliation" / "conciliacion_credinomina"
            / "doctype" / "cn_remittance_allocation"
            / "cn_remittance_allocation.json"
        )
        metadata = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("fx_evidence", metadata["field_order"])
        self.assertNotIn(
            "fx_evidence", {field["fieldname"] for field in metadata["fields"]}
        )

    def test_positive_rate_is_usable_without_justification(self):
        self.assertIn("36.5", remittance_fx_basis({"fx_rate": 36.5}))
        self.assertIn("36.5", remittance_fx_basis({
            "fx_rate": 36.5, "notes": "Pago de mayo",
        }))
        self.assertEqual("", remittance_fx_basis({"notes": "Tasa del convenio"}))
        self.assertEqual("", remittance_fx_basis({
            "support_file": "/private/files/comprobante.pdf",
        }))

    def test_nio_deposit_only_requires_a_positive_rate(self):
        source = (
            ROOT / "credinomina_reconciliation" / "conciliacion_credinomina"
            / "doctype" / "cn_remittance_allocation"
            / "cn_remittance_allocation.py"
        ).read_text(encoding="utf-8")
        metadata = json.loads((
            ROOT / "credinomina_reconciliation" / "conciliacion_credinomina"
            / "doctype" / "cn_remittance_allocation"
            / "cn_remittance_allocation.json"
        ).read_text(encoding="utf-8"))
        self.assertIn('if flt(self.fx_rate) <= 0:', source)
        self.assertNotIn("not remittance_fx_basis(self)", source)
        notes = next(field for field in metadata["fields"] if field["fieldname"] == "notes")
        self.assertFalse(notes.get("reqd"))

    def test_old_evidence_is_appended_once_to_notes(self):
        rows = [SimpleNamespace(
            name="CN-ALLOC-OLD", notes="Pago de mayo",
            fx_evidence="Comprobante bancario 123",
        )]
        fake_db = SimpleNamespace(
            has_column=Mock(return_value=True),
            sql=Mock(return_value=rows),
            set_value=Mock(),
        )
        with patch(
            "credinomina_reconciliation.patches.v1_0."
            "move_remittance_fx_evidence_to_notes.frappe.db",
            new=fake_db,
        ):
            move_old_evidence()
        fake_db.set_value.assert_called_once_with(
            "CN Remittance Allocation", "CN-ALLOC-OLD", "notes",
            "Pago de mayo\nFuente de la tasa de cambio: Comprobante bancario 123",
            update_modified=False,
        )


if __name__ == "__main__":
    unittest.main()
