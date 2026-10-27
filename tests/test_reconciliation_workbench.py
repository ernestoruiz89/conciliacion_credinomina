import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation import reconciliation_workbench as workbench


class WorkbenchFilterTests(unittest.TestCase):
    def setUp(self):
        permission = patch.object(frappe, 'has_permission', return_value=True)
        permission.start()
        self.addCleanup(permission.stop)

    def identity(self, **values):
        return dict(clients={'CLIENT-ID'}, numbers={'7593'}, number_values={'7593'},
                    national_ids=set(), national_values=set(), names={'josue'}, name_values={'Josue'}) | values

    def test_filter_uses_readable_unique_client_document_id(self):
        row = frappe._dict(name='CLIENT-ID', client_number='7593', national_id='', client_name='Josue')
        with patch.object(workbench, 'records', return_value=[row]) as read:
            self.assertEqual(workbench._filter_client('LALA', self.identity()), 'CLIENT-ID')
        self.assertEqual(read.call_args.args, ('CN Client',))
        self.assertEqual(read.call_args.kwargs['filters'], {'employer': 'LALA'})

    def test_number_can_resolve_a_client_without_a_stored_link(self):
        row = frappe._dict(name='CLIENT-ID', client_number='7593', national_id='', client_name='Josue')
        with patch.object(workbench, 'records', return_value=[row]):
            self.assertEqual(workbench._filter_client('LALA', self.identity(clients=set())), 'CLIENT-ID')

    def test_missing_ambiguous_and_conflicting_clients_do_not_get_selected(self):
        candidate = dict(client_number='7593', national_id='', client_name='Josue')
        for rows in ([], [frappe._dict(name='WRONG-ID', **candidate)],
                     [frappe._dict(name='CLIENT-ID', **(candidate | {'client_number': 'OTHER'}))]):
            with self.subTest(rows=rows), patch.object(workbench, 'records', return_value=rows):
                self.assertIsNone(workbench._filter_client('LALA', self.identity()))
        with patch.object(workbench, 'records', return_value=[frappe._dict(name=name, **candidate) for name in ('A', 'B')]):
            self.assertIsNone(workbench._filter_client('LALA', self.identity(clients=set())))

    def test_endpoint_returns_resolved_filter_but_keeps_origin_scope(self):
        identity = self.identity()
        with patch.object(workbench, '_scope', return_value=('LALA', identity)) as scope, \
             patch.object(workbench.related, '_related_pending', return_value=dict(applications=[], rows=[])), \
             patch.object(workbench, '_filter_client', return_value='CLIENT-ID'):
            result = workbench.get_data(context={'import_name': 'IMPORT', 'source_row_name': 'ROW'})
        self.assertEqual(result['client'], 'CLIENT-ID')
        self.assertEqual(result['employer'], 'LALA')
        scope.assert_called_once_with(None, None, {'import_name': 'IMPORT', 'source_row_name': 'ROW'})

    def test_origin_permissions_are_checked_before_resolving_filters(self):
        with patch.object(workbench, '_scope', side_effect=frappe.PermissionError), \
             patch.object(workbench, '_filter_client') as lookup:
            with self.assertRaises(frappe.PermissionError):
                workbench.get_data(context={'import_name': 'PRIVATE', 'source_row_name': 'ROW'})
        lookup.assert_not_called()

    def test_unreadable_client_does_not_block_origin_based_consultation(self):
        with patch.object(frappe, 'has_permission', return_value=False), patch.object(workbench, 'records') as read:
            self.assertIsNone(workbench._filter_client('LALA', self.identity()))
        read.assert_not_called()
