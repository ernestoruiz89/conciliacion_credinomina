import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.remittance_restore import prepare_restore


class RemittanceRestoreTests(unittest.TestCase):
    def document(self, **changes):
        doc = frappe._dict(
            flags=frappe._dict(from_restore=True), docstatus=2,
            accounting_source_key="ledger-key", source_file="ledger.xlsx", source_row=6355,
            result="Conciliado", allocated_usd=80, allocation_detail='[{"amount_usd":80}]',
            targets=[frappe._dict(idx=i, complementary_item=name, amount_usd=amount)
                     for i, name, amount in [(1, "CANCELED", -9.3), (2, "VALID", 10), (3, "MISSING", 5)]],
            detail_rows=[frappe._dict(client_name="Cliente", amount_usd=100,
                                     match_status="Conciliado", matched_targets='[{"amount_usd":80}]')],
        )
        doc.update(changes)
        doc.set = lambda field, value: doc.__setitem__(field, value)
        return doc

    @patch.object(frappe, "get_all", return_value=[frappe._dict(name="CANCELED", docstatus=2),
                                                  frappe._dict(name="VALID", docstatus=1)])
    def test_recovery_preserves_evidence_and_valid_targets_but_resets_results(self, query):
        doc = self.document()
        removed = prepare_restore(doc)
        self.assertEqual([row["partida"] for row in removed], ["CANCELED", "MISSING"])
        self.assertEqual(removed[0]["importe_usd"], -9.3)
        self.assertEqual([row.complementary_item for row in doc.targets], ["VALID"])
        self.assertEqual((doc.accounting_source_key, doc.source_file, doc.source_row),
                         ("ledger-key", "ledger.xlsx", 6355))
        self.assertEqual((doc.docstatus, doc.result, doc.allocated_usd, doc.allocation_detail),
                         (0, "Pendiente", 0, "[]"))
        self.assertEqual(doc.detail_rows[0].matched_targets, "[]")
        self.assertEqual(doc.detail_rows[0].amount_usd, 100)
        self.assertEqual(doc.targets[0].result, "Pendiente")

    @patch.object(frappe, "get_all")
    def test_normal_operations_manual_restores_and_submitted_snapshots_are_unchanged(self, query):
        for changes in ({"flags": frappe._dict()}, {"accounting_source_key": ""}, {"docstatus": 1}):
            doc = self.document(**changes)
            self.assertEqual(prepare_restore(doc), [])
            self.assertEqual(len(doc.targets), 3)
            self.assertEqual(doc.allocated_usd, 80)
        query.assert_not_called()

    @patch.object(frappe, "get_all", return_value=[frappe._dict(name="DRAFT", docstatus=0)])
    def test_draft_complementary_is_not_silently_removed(self, query):
        doc = self.document(targets=[frappe._dict(idx=1, complementary_item="DRAFT", amount_usd=10)])
        self.assertEqual(prepare_restore(doc), [])
        self.assertEqual(doc.docstatus, 2)
        self.assertEqual(doc.targets[0].complementary_item, "DRAFT")
