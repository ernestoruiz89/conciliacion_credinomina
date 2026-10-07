import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as api


class PeriodCycleEditingTests(unittest.TestCase):
    def period(self):
        previous = frappe._dict(name="P", employer="E", payroll_month="2026-09-01",
            reconciliation_mode="Operativa", collection_cycle="Segunda quincena",
            status="Pendiente", cutoff_date="2026-09-30", collection_rows=[])
        document = frappe._dict(previous.copy())
        document.collection_cycle = "Primera quincena"
        document.get_doc_before_save = lambda: previous
        document.is_new = lambda: False
        document._has_cycle_links = lambda: api.CNReconciliationPeriod._has_cycle_links(document)
        return document, previous

    def test_empty_pending_period_can_change_cycle_and_recalculates_cutoff(self):
        document, _ = self.period()
        with patch.object(api.frappe, "db", Mock(exists=Mock(return_value=False), get_value=Mock(return_value="Quincenal"))):
            api.CNReconciliationPeriod._validate_mode(document)
            api.CNReconciliationPeriod._set_due_date(document)
        self.assertEqual(str(document.cutoff_date), "2026-09-15")

    def test_real_links_block_changes_even_if_totals_are_zero(self):
        links = [("CN Source Row", "historical_period"), ("CN Source Row", "collection_period"),
                 ("CN Accounting Import", "historical_period"), ("CN Complementary Item", "period"),
                 ("CN Remittance Period", "period"), ("CN Remittance Target", "period")]
        for doctype, field in links:
            document, _ = self.period()
            with self.subTest(doctype=doctype, field=field), \
                 patch.object(api.frappe, "db", Mock(exists=Mock(side_effect=lambda dt, filters: dt == doctype and filters.get(field) == "P"))), \
                 patch.object(api.frappe, "throw", side_effect=frappe.ValidationError):
                with self.assertRaises(frappe.ValidationError):
                    api.CNReconciliationPeriod._validate_mode(document)

    def test_existing_collection_cannot_be_removed_to_change_cycle_in_same_save(self):
        document, previous = self.period()
        previous.collection_rows = [frappe._dict(name="ROW")]
        with patch.object(api.frappe, "db", Mock(exists=Mock(return_value=False))), \
             patch.object(api.frappe, "throw", side_effect=frappe.ValidationError):
            with self.assertRaises(frappe.ValidationError):
                api.CNReconciliationPeriod._validate_mode(document)

    def test_closed_empty_period_remains_protected(self):
        document, previous = self.period()
        previous.status = document.status = "Cerrado"
        with patch.object(api.frappe, "throw", side_effect=frappe.ValidationError):
            with self.assertRaises(frappe.ValidationError):
                api.CNReconciliationPeriod._validate_closed_transition(document)

    def test_normal_save_does_not_scan_additional_links(self):
        document, previous = self.period()
        document.collection_cycle = previous.collection_cycle
        document._has_cycle_links = Mock()
        with patch.object(api.frappe, "db", Mock(exists=Mock(return_value=False))):
            api.CNReconciliationPeriod._validate_mode(document)
        document._has_cycle_links.assert_not_called()

    def test_date_control_no_longer_requires_draft_status(self):
        metadata = json.loads(Path(api.__file__).with_suffix(".json").read_text(encoding="utf-8"))
        condition = next(field for field in metadata["fields"] if field["fieldname"] == "cutoff_date")["read_only_depends_on"]
        self.assertNotIn("Borrador", condition)
        self.assertIn("Cerrado", condition)
