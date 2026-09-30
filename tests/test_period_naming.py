import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from credinomina_reconciliation import period_naming as naming


class PeriodNamingTests(unittest.TestCase):
    def test_month_without_zero_year_from_payroll_and_literal_code(self):
        with (
            patch.object(naming.frappe, "db", Mock(get_value=Mock(return_value="EMP.YYYY"), exists=Mock(return_value=False))),
            patch.object(naming, "validate_name"),
            patch.object(naming, "getseries", return_value="01") as counter,
        ):
            self.assertEqual(naming.new_period_name("Empresa", "2025-04-30"), "EMP.YYYY-4-2025-01")
        counter.assert_called_once_with("EMP.YYYY-4-2025-", 2)

    def test_collision_uses_next_number_without_merging(self):
        with (
            patch.object(naming.frappe, "db", Mock(get_value=Mock(return_value="EMP"), exists=Mock(side_effect=[True, False]))),
            patch.object(naming, "validate_name"),
            patch.object(naming, "getseries", side_effect=["01", "02"]),
        ):
            self.assertEqual(naming.new_period_name("Empresa", "2025-04-01"), "EMP-4-2025-02")

    def test_changes_rename_persisted_links_and_open_form_and_child_parents(self):
        for employer, month in [("B", "2025-04-01"), ("A", "2025-05-01")]:
            with self.subTest(employer=employer, month=month):
                previous = SimpleNamespace(employer="A", payroll_month="2025-04-01")
                child = SimpleNamespace(parent="OLD")
                document = SimpleNamespace(name="OLD", employer=employer, payroll_month=month,
                    get_doc_before_save=lambda: previous, get_all_children=lambda: [child])
                with patch.object(naming, "new_period_name", return_value="NEW") as allocate, \
                     patch.object(naming.frappe, "rename_doc", return_value="NEW") as rename:
                    naming.rename_period_for_context_change(document)
                allocate.assert_called_once_with(employer, month)
                rename.assert_called_once_with(naming.DOCTYPE, "OLD", "NEW", force=True,
                                               merge=False, show_alert=False, rebuild_search=False)
                self.assertEqual((document.name, document.localname, child.parent), ("NEW", "OLD", "NEW"))

    def test_same_month_or_unrelated_edit_does_not_consume_sequence(self):
        document = SimpleNamespace(employer="A", payroll_month="2025-04-01",
            get_doc_before_save=lambda: SimpleNamespace(employer="A", payroll_month="2025-04-30"))
        with patch.object(naming, "new_period_name") as allocate, patch.object(naming.frappe, "rename_doc") as rename:
            naming.rename_period_for_context_change(document)
        allocate.assert_not_called()
        rename.assert_not_called()

    def test_failed_rename_does_not_change_in_memory_identity(self):
        document = SimpleNamespace(name="OLD", employer="B", payroll_month="2025-04-01",
            get_doc_before_save=lambda: SimpleNamespace(employer="A", payroll_month="2025-04-01"))
        with patch.object(naming, "new_period_name", return_value="NEW"), \
             patch.object(naming.frappe, "rename_doc", side_effect=ValueError):
            with self.assertRaises(ValueError):
                naming.rename_period_for_context_change(document)
        self.assertEqual(document.name, "OLD")

