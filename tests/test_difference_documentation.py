import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import difference_documentation as module
from credinomina_reconciliation.manual_items_review import execute, ITEM
from credinomina_reconciliation.manual_complementary_policy import guard_new_item, imported_item
from credinomina_reconciliation.rounding import rounding_movements

IMPORT = "CN Accounting Import"


class DifferenceDocumentationTests(unittest.TestCase):
    def test_deposit_detail_comments_can_be_omitted_or_cleared_without_financial_writes(self):
        row = frappe._dict(name='DETAIL', doctype='CN Remittance Detail', difference_reason='', difference_notes='Anterior')
        document = Mock(doctype='CN Remittance Allocation', docstatus=1, modified='v1')
        document.name = 'DEP'
        document.get.return_value = [row]
        reason = Mock()
        with patch.object(frappe, 'get_doc', side_effect=lambda doctype, name: reason if doctype == 'CN Difference Reason' else document), \
                patch.object(frappe.db, 'exists', return_value=True), \
                patch.object(frappe.db, 'get_value'), patch.object(frappe.db, 'set_value') as write, \
                patch.object(frappe, 'clear_document_cache'):
            for comments in ({}, {'difference_notes': None}, {'difference_notes': ''}, {'difference_notes': '   '}):
                with self.subTest(comments=comments):
                    result = module.document_difference('CN Remittance Allocation', 'DEP', 'v1', 'Motivo',
                        parentfield='detail_rows', row_name='DETAIL', **comments)
                    self.assertEqual(result, {'difference_reason': 'Motivo', 'difference_notes': ''})
                    self.assertEqual(write.call_args_list[-2].args,
                        ('CN Remittance Detail', 'DETAIL', {'difference_reason': 'Motivo', 'difference_notes': ''}))
            document.save.assert_not_called()
        self.assertEqual(row.difference_notes, 'Anterior')

    def test_comments_stay_required_outside_deposit_detail(self):
        row = frappe._dict(name='SOURCE', doctype='CN Source Row')
        document = Mock(doctype='CN Accounting Import', docstatus=0, modified='v1')
        document.get.return_value = [row]
        with patch.object(frappe, 'get_doc', return_value=document), \
                patch.object(frappe.db, 'exists', return_value=True), patch.object(frappe.db, 'get_value'), \
                patch.object(frappe.db, 'set_value') as write:
            with self.assertRaisesRegex(frappe.ValidationError, 'Documente el motivo'):
                module.document_difference('CN Accounting Import', 'IMP', 'v1', 'Motivo', parentfield='rows', row_name='SOURCE')
            write.assert_not_called()

    def test_target_requires_exact_parent_and_child(self):
        row = frappe._dict(name="R", doctype="CN Source Row")
        doc = frappe._dict(doctype=IMPORT, rows=[row])
        self.assertIs(module.target(doc, "rows", "R"), row)
        with patch.object(module.frappe, "throw", side_effect=ValueError):
            for field, name in (("targets", "R"), ("rows", "OTHER"), ("", "")):
                with self.assertRaises(ValueError):
                    module.target(doc, field, name)

    def test_new_manual_and_old_draft_submission_are_blocked(self):
        for new, status, previous_status, key, blocked in (
            (True, 0, None, "", True), (False, 1, 0, "", True),
            (False, 1, 1, "", False), (True, 0, None, "CORE", True),
        ):
            doc = SimpleNamespace(is_new=lambda: new, docstatus=status,
                get=lambda name: key, get_doc_before_save=lambda: None if previous_status is None else SimpleNamespace(docstatus=previous_status))
            with patch.object(frappe, "throw", side_effect=ValueError):
                if blocked:
                    with self.assertRaises(ValueError): guard_new_item(doc)
                else:
                    guard_new_item(doc)

    def test_import_service_can_create_core_evidence_but_context_cannot_escape(self):
        doc = SimpleNamespace(is_new=lambda: True, docstatus=0, get=lambda name: "KEY",
                              get_doc_before_save=lambda: None)
        with patch.object(frappe, "throw", side_effect=ValueError):
            with imported_item("KEY"):
                guard_new_item(doc)
            with self.assertRaises(ValueError):
                guard_new_item(doc)

    def test_even_authorized_positive_and_negative_tolerances_do_not_absorb_cash(self):
        for paid, applied in ((46.53, 46.52), (46.52, 46.53)):
            args = ([{"id": "D", "amount_usd": paid}], [{"id": "H:A", "amount_usd": applied}],
                    [{"deposit_id": "D", "claim_id": "H:A", "amount_usd": min(paid, applied)}],
                    {"D": max(paid-applied, 0)}, {"H:A": max(applied-paid, 0)}, {"E": .10})
            before = copy.deepcopy(args)
            self.assertEqual(rounding_movements(*args), [])
            self.assertEqual(before, args)


class ManualReviewTests(unittest.TestCase):
    def test_inventory_reads_only_manual_items_with_filters_and_no_link_expansion(self):
        items = [frappe._dict(name="M", employer="E", category="Otros ingresos", amount_usd=10,
                             applied_usd=3, pending_usd=7, reconciliation_status="Parcial"),
                 frappe._dict(name="T", employer="E", category="Diferencia por tolerancia", amount_usd=1),
                 frappe._dict(name="U", employer="E", category="Otros ingresos", amount_usd=5,
                              applied_usd=2, pending_usd=3, reconciliation_status="Saldo no disponible")]
        with patch("credinomina_reconciliation.manual_items_review.records", return_value=items) as records, \
             patch.object(frappe, "has_permission", return_value=True):
            columns, rows, message = execute({"employer": "E", "item": "M", "from_date": "2025-01-01",
                                              "to_date": "2025-12-31", "include_tolerance": "0", "show_details": 1})
        self.assertEqual([row["item"] for row in rows], ["M", "U"])
        self.assertEqual((rows[0]["amount_usd"], rows[0]["applied_usd"], rows[0]["pending_usd"]), (10, 3, 7))
        self.assertIsNone(rows[1]["pending_usd"])
        self.assertNotIn("relation", [col["fieldname"] for col in columns])
        self.assertIn("no consulta ni relaciona", message)
        records.assert_called_once()
        self.assertEqual(records.call_args.args, (ITEM,))
        self.assertEqual(records.call_args.kwargs["filters"], {
            "accounting_source_key": ["is", "not set"], "employer": "E", "name": "M",
            "posting_date": ["between", ["2025-01-01", "2025-12-31"]]})

    def test_tolerance_is_included_only_when_requested(self):
        rows = [frappe._dict(name="T", category="Diferencia por tolerancia"),
                frappe._dict(name="L", category="Otros ingresos", movement_key="old")]
        with patch("credinomina_reconciliation.manual_items_review.records", return_value=rows), \
             patch.object(frappe, "has_permission", return_value=True):
            self.assertEqual(execute({"include_tolerance": 0})[1], [])
            self.assertEqual([row["item"] for row in execute({"include_tolerance": 1})[1]], ["T", "L"])

    def test_permission_and_date_validation(self):
        with patch.object(frappe, "has_permission", return_value=False):
            with self.assertRaises(frappe.PermissionError):
                execute({})
        with patch.object(frappe, "has_permission", return_value=True):
            with self.assertRaises(frappe.ValidationError):
                execute({"from_date": "2025-02-01", "to_date": "2025-01-01"})


if __name__ == "__main__":
    unittest.main()
