import json
import unittest
from collections import defaultdict
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.application_quality import collection_quality, source_quality
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


class ApplicationQualityTests(unittest.TestCase):
    def row(self, **values):
        return frappe._dict(dict(expected_usd=100, expected_nio=3662.43,
            deducted_usd=0, deducted_nio=0, applied_usd=100, complementary_usd=0,
            deduction_status="Pendiente de detalle", **values))

    def test_collection_is_valid_basis_without_fabricating_deduction_or_cash(self):
        row = self.row()
        before = dict(row)
        result = collection_quality(row)
        self.assertEqual(result["quality_status"], "Conforme según cobranza")
        self.assertEqual(result["quality_difference_usd"], 0)
        self.assertEqual(dict(row), before)

    def test_partial_and_one_cent_are_not_conformity(self):
        for applied, expected in [(80, -20), (99.99, -0.01), (100.01, 0.01)]:
            row = self.row()
            row.applied_usd = applied
            result = collection_quality(row)
            self.assertFalse(result["quality_status"].startswith("Conforme"))
            self.assertEqual(result["quality_difference_usd"], expected)

    def test_late_response_changes_basis_and_result(self):
        row = self.row()
        row.update(deduction_status="Deduccion parcial", deducted_usd=80)
        self.assertEqual(collection_quality(row)["quality_difference_usd"], 20)
        row.applied_usd = 80
        self.assertEqual(collection_quality(row)["quality_status"], "Conforme según deducción")

    def test_invalid_deduction_does_not_fall_back_to_collection(self):
        row = self.row()
        row.deduction_status = "Importes inconsistentes"
        result = collection_quality(row)
        self.assertEqual(result["quality_status"], "Revisar base de comparación")
        self.assertIsNone(result["quality_difference_usd"])

    def test_nio_conversion_requires_basis(self):
        row = self.row()
        row.update(deduction_status="Deduccion total", deducted_nio=3662.43)
        self.assertEqual(collection_quality(row)["quality_status"], "Conforme según deducción")
        row.expected_usd = 0
        self.assertEqual(collection_quality(row)["quality_status"], "Revisar base de comparación")

    def test_no_deduction_and_no_application_is_quality_conformity(self):
        row = self.row()
        row.update(deduction_status="No deducido", applied_usd=0)
        self.assertEqual(collection_quality(row)["quality_status"], "Conforme según deducción")

    def test_identified_complement_not_counted_as_loan_application(self):
        row = self.row()
        row.update(applied_usd=90, complementary_usd=10)
        self.assertEqual(collection_quality(row)["quality_status"], "Conforme según cobranza")

    def test_multiple_quotas_cannot_net_their_differences(self):
        first, second = self.row(), self.row()
        first.applied_usd, second.applied_usd = 80, 120
        self.assertEqual(source_quality([first, second])["quality_status"], "Con diferencias")
        self.assertEqual(source_quality([])["quality_status"], "Sin cobranza vinculada")

    def match(self, amount=100, other=None, reference=None):
        row = self.row()
        row.update(name="C1", parent="P1", loan_number="109-1", client_number="1", applied_usd=0)
        row.application_reference = reference
        row.as_dict = lambda: dict(row)
        source = frappe._dict(name="A1", event_type="Aplicacion", effective=1,
            currency="USD", amount=amount, event_date="2026-09-15", loan_number="109-1", client_number="1")
        source.as_dict = lambda: dict(source)
        source.reference = reference
        period = frappe._dict(name="P1", employer="E1", reconciliation_mode="Operativa")
        resolver = Mock()
        resolver.resolve.return_value = ("E1", "")
        with patch.object(engine, "load_client_index", return_value=[]), \
             patch.object(engine.frappe, "get_all", return_value=[]), \
             patch.object(engine, "attach_employer_aliases"), \
             patch.object(engine, "AccountingEmployerResolver", return_value=resolver):
            engine._match_applications([source], [row] + ([other] if other else []), [], defaultdict(list), [period])
        return source

    def test_engine_links_collection_without_provisional_status(self):
        source = self.match()
        self.assertEqual(source.match_status, "Conciliado")
        self.assertIn("deducción pendiente", source.match_reason)
        self.assertEqual(json.loads(source.application_allocation_detail)[0]["amount_usd"], 100)

    def test_engine_does_not_force_an_excess_into_collection(self):
        self.assertEqual(self.match(120).match_status, "Sin coincidencia")

    def test_explicit_reference_identifies_excess_for_quality_not_cash(self):
        source = self.match(120, reference="EXPLICIT")
        self.assertEqual(source.match_status, "Conciliado")
        self.assertIn("excede la base", source.match_reason)
        self.assertEqual(json.loads(source.application_allocation_detail)[0]["amount_usd"], 120)

    def test_repeated_explicit_reference_still_does_not_guess_excess_destination(self):
        other = self.row()
        other.update(name="C2", parent="P1", loan_number="109-1", client_number="1", application_reference="EXPLICIT")
        other.as_dict = lambda: dict(other)
        self.assertEqual(self.match(120, other, "EXPLICIT").match_status, "Sin coincidencia")

    def test_ambiguous_identity_is_not_forced_into_one_quota(self):
        other = self.row()
        other.update(name="C2", parent="P1", loan_number="109-1", client_number="1")
        other.as_dict = lambda: dict(other)
        self.assertEqual(self.match(other=other).match_status, "Ambiguo")


if __name__ == "__main__":
    unittest.main()
