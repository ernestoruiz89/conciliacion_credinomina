import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation.patches.v1_0 import select_application_quality_basis as migration


class ApplicationBasisPatchTests(unittest.TestCase):
    def period(self, **values):
        return frappe._dict(dict(name="P", doctype="CN Reconciliation Period",
            collection_rows=[], provisional_adjustments=[], **values))

    def test_whole_period_choice_from_existing_evidence(self):
        period = self.period()
        self.assertEqual(migration.choose_basis(period), "Cobranza")
        period.collection_rows = [frappe._dict(deduction_status="Pendiente de detalle"),
                                  frappe._dict(deduction_status="Deduccion parcial")]
        self.assertEqual(migration.choose_basis(period), "Detalle de empresa")

    def test_materialized_base_takes_precedence_and_mixed_is_not_guessed(self):
        period = self.period(employer_response_file="/private/files/detail.xlsx")
        period.provisional_adjustments = [frappe._dict(state="Materializado", basis="Cobranza")]
        self.assertEqual(migration.choose_basis(period), "Cobranza")
        period.provisional_adjustments.append(frappe._dict(state="Materializado", basis="Detalle de deducción"))
        self.assertIsNone(migration.choose_basis(period))

    def test_patch_does_not_recalculate_or_replace_uploaded_data(self):
        period = self.period(collection_file="collection.xlsx", employer_response_file="detail.xlsx")
        period.save = Mock()
        period.provisional_adjustments = [frappe._dict(doctype="CN Provisional Adjustment", name="A", state="Aprobado")]
        db = Mock()
        with patch.object(migration.frappe, "get_all", return_value=["P"]), \
             patch.object(migration.frappe, "get_doc", return_value=period), \
             patch.object(migration.frappe, "db", db):
            migration.execute()
            self.assertEqual(db.set_value.call_count, 2)
            db.set_value.assert_any_call(period.doctype, "P", "application_basis", "Detalle de empresa", update_modified=False)
            period.application_basis = "Detalle de empresa"
            db.reset_mock()
            migration.execute()
            db.set_value.assert_not_called()
        period.save.assert_not_called()
        self.assertEqual((period.collection_file, period.employer_response_file), ("collection.xlsx", "detail.xlsx"))
