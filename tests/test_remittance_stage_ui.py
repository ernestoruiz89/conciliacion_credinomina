"""The form must distinguish importing evidence from confirming cash."""

import unittest
from pathlib import Path


FORM = (
    Path(__file__).resolve().parents[1]
    / "credinomina_reconciliation"
    / "conciliacion_credinomina"
    / "doctype"
    / "cn_remittance_allocation"
    / "cn_remittance_allocation.js"
)


class RemittanceStageUiTest(unittest.TestCase):
    def test_draft_deposit_requires_explicit_supervisor_confirmation(self):
        source = FORM.read_text(encoding="utf-8")
        self.assertIn('frm.get_perm(0, "submit")', source)
        self.assertIn('frm.savesubmit()', source)
        self.assertIn("todavía no participa en la conciliación", source)

    def test_detail_can_be_loaded_after_deposit_confirmation(self):
        source = FORM.read_text(encoding="utf-8")
        self.assertIn('frm.doc.docstatus === 2', source)
        self.assertIn('Cargar detalle del depósito', source)
        self.assertIn('pendiente de detalle por cliente', source)


if __name__ == "__main__":
    unittest.main()
