import copy
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import accounting_related_records as related
from credinomina_reconciliation import complementary_related_client as resolver
from credinomina_reconciliation import reconciliation_workbench as workbench


class RemittanceHeaderPendingTests(unittest.TestCase):
    def setUp(self):
        self.doc = frappe._dict(name='DEP', employer='EMP', source_client_name='José Pérez',
            source_loan_number='', detail_rows=[], check_permission=Mock())
        self.client = frappe._dict(name='123', client_number='123', employer='EMP',
            client_name='JOSE PEREZ', national_id='CEDULA')
        for replacement in (patch.object(frappe, 'has_permission', return_value=True),
                            patch.object(resolver, 'records', return_value=[self.client]),
                            patch.object(resolver, 'child_records', return_value=[])):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_saved_header_resolves_client_without_detail_and_never_writes(self):
        before = copy.deepcopy(self.doc)
        with patch.object(frappe, 'get_doc', return_value=self.doc), \
             patch.object(related, '_related_pending', return_value=dict(applications=[], rows=[])) as pending, \
             patch.object(frappe.db, 'set_value') as write:
            result = related.get_related_pending_for_remittance('DEP')
        self.assertEqual(result['context'], {'remittance_name': 'DEP'})
        self.assertEqual(result['employer'], 'EMP')
        company, identity = pending.call_args.args
        self.assertEqual((company, identity['client_number']), ('EMP', '123'))
        self.assertEqual(identity['clients'], {'123'})
        self.assertEqual(self.doc.source_client_name, before.source_client_name)
        self.assertEqual(self.doc.detail_rows, [])
        write.assert_not_called()
        self.doc.check_permission.assert_called_with('read')

    def test_header_uses_known_alias_and_credit_and_rejects_conflicting_identity(self):
        self.doc.source_client_name = 'Pepe Pérez'
        self.doc.source_loan_number = '00123-1'
        with patch.object(resolver, 'child_records', return_value=[frappe._dict(parent='123', alias_name='Pepe Pérez')]), \
             patch.object(resolver, '_loan_owners', return_value={'123'}) as owners:
            company, identity = related._remittance_identity(self.doc)
            self.assertEqual(identity['client_number'], '123')
            self.assertIn('Pepe Pérez', identity['name_values'])
            owners.assert_called_once()
        with patch.object(resolver, '_loan_owners', return_value={'OTHER'}):
            with self.assertRaises(frappe.ValidationError):
                related._remittance_identity(self.doc)

    def test_detail_entry_point_keeps_its_own_client_and_company(self):
        row = frappe._dict(name='ROW', employer='BENEFICIARY', client='456', client_number='456', client_name='Other')
        self.doc.detail_rows = [row]
        company, identity = related._remittance_identity(self.doc, 'ROW')
        self.assertEqual((company, identity['clients']), ('BENEFICIARY', {'456'}))
        with self.assertRaises(frappe.ValidationError):
            related._remittance_identity(self.doc, 'NOT-A-ROW')

    def test_blank_unknown_and_ambiguous_header_cannot_return_an_unrelated_clients_pending(self):
        for name in ('', 'Sin identificar', 'Unknown'):
            self.doc.source_client_name = name
            with self.assertRaises(frappe.ValidationError):
                related._remittance_identity(self.doc)
        self.doc.source_client_name = 'José Pérez'
        with patch.object(resolver, 'records', return_value=[self.client, frappe._dict(self.client, name='OTHER', client_number='OTHER')]):
            with self.assertRaises(frappe.ValidationError):
                related._remittance_identity(self.doc)
        self.doc.employer = ''
        with self.assertRaises(frappe.ValidationError):
            related._remittance_identity(self.doc)

    def test_origin_permission_precedes_client_or_pending_reads(self):
        self.doc.check_permission.side_effect = frappe.PermissionError
        with patch.object(frappe, 'get_doc', return_value=self.doc), \
             patch.object(related, '_remittance_identity') as identity:
            with self.assertRaises(frappe.PermissionError):
                related.get_related_pending_for_remittance('DEP')
            identity.assert_not_called()

    def test_workbench_can_reopen_same_header_context(self):
        employer = frappe._dict(check_permission=Mock())
        with patch.object(frappe, 'get_doc', side_effect=[self.doc, employer]):
            company, identity = workbench._scope(context={'remittance_name': 'DEP'})
        self.assertEqual((company, identity['client_number']), ('EMP', '123'))
        employer.check_permission.assert_called_once_with('read')
