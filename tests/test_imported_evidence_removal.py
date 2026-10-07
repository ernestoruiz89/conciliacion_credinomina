import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import CNRemittanceAllocation
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item.cn_complementary_item import CNComplementaryItem
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import CNAccountingImport
from credinomina_reconciliation import accounting_evidence as evidence


def reject(message, *args, **kwargs):
    raise ValueError(message)


class ImportedEvidenceRemovalTests(unittest.TestCase):
    def setUp(self):
        role_patch = patch.object(evidence, "has_core_removal_role", return_value=False)
        self.role = role_patch.start()
        self.addCleanup(role_patch.stop)

    def test_controllers_block_cancel_and_delete_for_every_imported_state(self):
        for controller, doctype in ((CNRemittanceAllocation, "CN Remittance Allocation"),
                                    (CNComplementaryItem, "CN Complementary Item"),
                                    (CNAccountingImport, "CN Accounting Import")):
            for status in (0, 1, 2):
                for hook in ("before_cancel", "on_trash"):
                    with self.subTest(doctype=doctype, status=status, hook=hook), \
                         patch.object(frappe, "_", side_effect=lambda text: text), \
                         patch.object(frappe, "throw", side_effect=reject):
                        doc = frappe._dict(doctype=doctype, name="IMPORTED", docstatus=status,
                                           accounting_source_key="original-ledger-key", file_hash="original-hash")
                        with self.assertRaisesRegex(ValueError, "histórico contable"):
                            getattr(controller, hook)(doc)

    def test_clearing_origin_in_request_does_not_bypass_saved_evidence(self):
        doc = frappe._dict(doctype="CN Remittance Allocation", name="IMPORTED", accounting_source_key="")
        with patch.object(frappe, "db", Mock(get_value=Mock(return_value="saved-key"))) as db, \
             patch.object(frappe, "_", side_effect=lambda text: text), \
             patch.object(frappe, "throw", side_effect=reject):
            with self.assertRaisesRegex(ValueError, "Use Desconciliar"):
                CNRemittanceAllocation.before_cancel(doc)
            db.get_value.assert_called_once_with(doc.doctype, doc.name, "accounting_source_key")

    def test_manual_deposits_keep_cancellation_and_deletion(self):
        doc = frappe._dict(doctype="CN Remittance Allocation", name="MANUAL", accounting_source_key="",
                           _assert_can_reverse_distribution=Mock())
        with patch.object(frappe, "db", Mock(get_value=Mock(return_value=None))):
            CNRemittanceAllocation.before_cancel(doc)
            CNRemittanceAllocation.on_trash(doc)
        doc._assert_can_reverse_distribution.assert_called_once_with()

    def test_authorized_deposit_still_checks_distribution(self):
        self.role.return_value = True
        doc = frappe._dict(doctype="CN Remittance Allocation", accounting_source_key="core",
                           _assert_can_reverse_distribution=Mock(side_effect=ValueError("closed period")))
        with self.assertRaisesRegex(ValueError, "closed period"):
            CNRemittanceAllocation.before_cancel(doc)

    def test_authorized_accounting_import_still_checks_closed_periods(self):
        self.role.return_value = True
        doc = frappe._dict(doctype="CN Accounting Import", file_hash="core", rows=[],
                           _assert_no_closed_period_links=Mock(side_effect=ValueError("closed period")))
        for hook in ("on_trash", "before_cancel"):
            with self.assertRaisesRegex(ValueError, "closed period"):
                getattr(CNAccountingImport, hook)(doc)

    def test_authorized_complementary_item_still_checks_financial_dependencies(self):
        self.role.return_value = True
        doc = frappe._dict(doctype="CN Complementary Item", accounting_source_key="core")
        with patch("credinomina_reconciliation.receivable_recovery.guard_origin",
                   side_effect=ValueError("registered recovery")):
            for hook in ("on_trash", "before_cancel"):
                with self.assertRaisesRegex(ValueError, "registered recovery"):
                    getattr(CNComplementaryItem, hook)(doc)

    def test_accounting_import_provenance_and_cleared_payload(self):
        for values in ({"file_hash": "hash"}, {"bulk_source_hash": "bulk"},
                       {"imported_on": "2026-10-07"},
                       {"source_file": "/private/files/core.csv", "rows": [frappe._dict(source_row=2)]},
                       {"rows": [frappe._dict(accounting_source_key="key")]}):
            with self.subTest(values=values):
                self.assertTrue(evidence._is_core_import(frappe._dict(doctype="CN Accounting Import", **values)))
        doc = frappe._dict(doctype="CN Accounting Import", name="SAVED", rows=[])
        for saved, child in (({"file_hash": "saved"}, False),
                             ({"source_file": "/private/files/core.csv"}, True),
                             ({}, True)):
            with self.subTest(saved=saved), patch.object(frappe, "db", Mock(
                get_value=Mock(return_value=saved), exists=Mock(return_value=child)
            )):
                self.assertTrue(evidence._is_core_import(doc))

    def test_unprocessed_accounting_draft_is_not_protected(self):
        doc = frappe._dict(doctype="CN Accounting Import", name="DRAFT", rows=[], source_file="/private/files/new.csv")
        with patch.object(frappe, "db", Mock(
            get_value=Mock(return_value={"source_file": doc.source_file}), exists=Mock(return_value=False)
        )):
            self.assertFalse(evidence._is_core_import(doc))


class ExplicitCoreRoleTests(unittest.TestCase):
    def test_requires_enabled_role_and_explicit_user_assignment_even_for_administrator(self):
        for user in ("Administrator", "supervisor@example.test", "operator@example.test"):
            for enabled, assigned in ((True, True), (True, False), (False, True)):
                with self.subTest(user=user, enabled=enabled, assigned=assigned), \
                     patch.object(frappe, "session", frappe._dict(user=user)), \
                     patch.object(frappe, "db", Mock(exists=Mock(side_effect=[enabled, assigned]))) as db:
                    self.assertEqual(evidence.has_core_removal_role(), enabled and assigned)
                    if enabled:
                        db.exists.assert_called_with("Has Role", {
                            "parent": user, "parenttype": "User", "parentfield": "roles",
                            "role": evidence.CORE_REMOVAL_ROLE,
                        })
