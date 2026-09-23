"""The remittance FX source lives in notes or its support, not a separate field."""

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

    def test_source_must_be_in_notes_or_support(self):
        self.assertEqual("", remittance_fx_basis({"notes": "Pago de mayo"}))
        self.assertIn("Tasa", remittance_fx_basis({
            "notes": "Tasa según convenio empresarial del 1 de mayo"
        }))
        self.assertIn("/private/files/", remittance_fx_basis({
            "support_file": "/private/files/comprobante.pdf",
            "notes": "Pago de mayo",
        }))

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
