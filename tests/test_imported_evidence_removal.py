import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import CNRemittanceAllocation
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_complementary_item.cn_complementary_item import CNComplementaryItem


def reject(message, *args, **kwargs):
    raise ValueError(message)


class ImportedEvidenceRemovalTests(unittest.TestCase):
    def test_controllers_block_cancel_and_delete_for_every_imported_state(self):
        for controller, doctype in ((CNRemittanceAllocation, "CN Remittance Allocation"),
                                    (CNComplementaryItem, "CN Complementary Item")):
            for status in (0, 1, 2):
                for hook in ("before_cancel", "on_trash"):
                    with self.subTest(doctype=doctype, status=status, hook=hook), \
                         patch.object(frappe, "_", side_effect=lambda text: text), \
                         patch.object(frappe, "throw", side_effect=reject):
                        doc = frappe._dict(doctype=doctype, name="IMPORTED", docstatus=status,
                                           accounting_source_key="original-ledger-key")
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
