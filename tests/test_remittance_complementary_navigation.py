import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import (
    get_linked_complementary_items,
)


class LinkedComplementaryItemsTests(unittest.TestCase):
    def document(self):
        doc = frappe._dict(name="DEP", targets=[frappe._dict(complementary_item="TARGET")],
            detail_rows=[frappe._dict(difference_core_item="CORE")],
            allocation_detail='[{"tipo":"Partida complementaria","partida":"AUTO"},'
                              '{"tipo":"Aplicación","partida":"NOT-COMPLEMENTARY"}]')
        doc.check_permission = Mock()
        return doc

    @patch("frappe.has_permission", return_value=True)
    @patch("frappe.get_doc")
    @patch("credinomina_reconciliation.report_records.records")
    def test_all_link_types_are_scoped_and_record_permissions_are_respected(self, records, get_doc, _permission):
        doc = self.document()
        get_doc.return_value = doc
        records.side_effect = [
            [frappe._dict(name="DIRECT"), frappe._dict(name="AUTO")],
            [frappe._dict(name="AUTO"), frappe._dict(name="CORE")],  # TARGET is not readable
        ]
        self.assertEqual(get_linked_complementary_items("DEP"), ["AUTO", "CORE", "DIRECT"])
        doc.check_permission.assert_called_once_with("read")
        self.assertEqual(records.call_args_list[0].kwargs["or_filters"],
                         {"registered_deposit": "DEP", "origin_deposit": "DEP"})
        self.assertEqual(records.call_args_list[1].kwargs["filters"],
                         {"name": ["in", ["AUTO", "CORE", "TARGET"]]})

    @patch("frappe.has_permission", return_value=True)
    @patch("frappe.get_doc")
    @patch("credinomina_reconciliation.report_records.records")
    def test_empty_deposit_does_not_query_the_unrestricted_list(self, records, get_doc, _permission):
        doc = self.document()
        doc.targets, doc.detail_rows, doc.allocation_detail = [], [], "[]"
        get_doc.return_value = doc
        records.return_value = []
        self.assertEqual(get_linked_complementary_items("DEP"), [])
        self.assertEqual(records.call_count, 1)

    @patch("frappe.has_permission", return_value=True)
    @patch("frappe.get_doc")
    @patch("credinomina_reconciliation.report_records.records")
    def test_corrupt_allocation_is_not_silently_ignored(self, records, get_doc, _permission):
        doc = self.document()
        doc.allocation_detail = "invalid"
        get_doc.return_value = doc
        with self.assertRaises(frappe.ValidationError):
            get_linked_complementary_items("DEP")
        records.assert_not_called()
