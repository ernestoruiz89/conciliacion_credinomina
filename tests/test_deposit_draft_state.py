import copy
import unittest

import frappe

from credinomina_reconciliation.deposit_draft_state import reset_unexecuted


class DepositDraftStateTests(unittest.TestCase):
    def deposit(self, **changes):
        return frappe._dict(dict(docstatus=0, allocated_usd=100, justified_surplus_usd=10,
            allocation_detail='[{"tipo":"Aplicacion historica","importe_usd":100}]', result='Conciliado',
            targets=[frappe._dict(historical_application='APP', amount_usd=100, result='Aplicada')],
            detail_rows=[frappe._dict(client='CLIENT', deducted_usd=100, linked_usd=100,
                client_credit_usd=10, matched_targets='[{"id":"APP"}]', match_status='Conciliada')]), **changes)

    def test_duplicate_and_submitted_insert_cannot_inherit_execution(self):
        for status in (0, 1):
            with self.subTest(status=status):
                document = self.deposit(docstatus=status)
                self.assertTrue(reset_unexecuted(document))
                self.assertEqual((document.allocated_usd, document.justified_surplus_usd,
                    document.allocation_detail, document.result), (0, 0, '[]', 'Pendiente'))
                self.assertEqual(document.targets[0].result, 'Pendiente')
                self.assertEqual((document.targets[0].historical_application, document.targets[0].amount_usd), ('APP', 100))
                self.assertEqual((document.detail_rows[0].client, document.detail_rows[0].deducted_usd), ('CLIENT', 100))
                self.assertEqual((document.detail_rows[0].linked_usd, document.detail_rows[0].matched_targets), (0, '[]'))

    def test_draft_edit_and_submission_still_require_explicit_reconciliation(self):
        for status in (0, 1):
            with self.subTest(status=status):
                document = self.deposit(docstatus=status)
                self.assertTrue(reset_unexecuted(document, frappe._dict(docstatus=0)))
                self.assertEqual((document.allocated_usd, document.allocation_detail), (0, '[]'))

    def test_submitted_distribution_and_cancelled_restore_remain_audit_evidence(self):
        for document, previous in ((self.deposit(docstatus=1), frappe._dict(docstatus=1)),
                (self.deposit(docstatus=2), frappe._dict(docstatus=1)), (self.deposit(docstatus=2), None)):
            with self.subTest(status=document.docstatus, previous=previous):
                frozen = copy.deepcopy(document)
                self.assertFalse(reset_unexecuted(document, previous))
                self.assertEqual(document, frozen)
