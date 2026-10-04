import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import accounting_client_summary as summary


class AccountingClientSummaryTests(unittest.TestCase):
    def source(self, **changes):
        return dict(dict(name='A', event_type='Aplicacion', effective=1, client_number='1',
            client_name='Cliente uno', currency='USD', amount=100, historical_period='H',
            match_status='Conciliado'), **changes)

    def build(self, rows, collections=None):
        return summary.build_summary({'rows': rows}, collections or {})

    def test_two_deposits_three_applications_grouped_by_customer(self):
        rows = self.build([self.source(amount=163.68), self.source(amount=88.60, historical_remitted_usd=22.15),
                           self.source(client_number='2', client_name='Cliente dos', amount=106.48, historical_remitted_usd=53.24)])
        by_number = {row['client_number']: row for row in rows}
        self.assertEqual([by_number['1'][k] for k in ('applied_usd', 'assigned_usd', 'balance_usd')], [252.28, 22.15, 230.13])
        self.assertEqual([by_number['2'][k] for k in ('applied_usd', 'assigned_usd', 'balance_usd')], [106.48, 53.24, 53.24])
        self.assertAlmostEqual(sum(row['balance_usd'] for row in rows), 283.37)

    def test_confirmed_adjustment_not_subtracted_twice(self):
        row = self.build([self.source(application_adjustment_usd=20, historical_remitted_usd=30)])[0]
        self.assertEqual((row['applied_usd'], row['assigned_usd'], row['balance_usd']), (80, 30, 50))
        row = self.build([self.source(application_adjustment_usd=100)])[0]
        self.assertEqual(row['status'], 'Conciliado')

    def test_signed_tolerance_and_nio_conversion(self):
        for gross, cash, delta in ((46.52, 46.53, .01), (46.53, 46.52, -.01)):
            row = self.build([self.source(amount=gross, historical_remitted_usd=cash,
                historical_detail=[{'diferencia_usd': delta}])])[0]
            self.assertEqual((row['balance_usd'], row['status']), (0, 'Conciliado'))
        row = self.build([self.source(currency='NIO', amount=974.94, manual_fx_rate=36.6243)])[0]
        self.assertEqual(row['applied_usd'], 26.62)

    def test_missing_fx_is_unknown_and_ignored_rows_excluded(self):
        row = self.build([self.source(currency='NIO'), self.source(event_type='Ajuste'),
            self.source(effective=0), self.source(match_status='Ignorado')])[0]
        self.assertIsNone(row['applied_usd'])
        self.assertIsNone(row['balance_usd'])
        self.assertEqual(row['applications'], 1)

    def test_homonyms_without_identifiers_are_not_merged(self):
        rows = self.build([self.source(name='A', client_number=''), self.source(name='B', client_number='')])
        self.assertEqual(len(rows), 2)

    def test_shared_operational_claim_counts_cash_once(self):
        collection = dict(applied_usd=100, remittance_detail=[{'importe_usd': 40},
                          {'destino': 'Partida complementaria', 'importe_usd': 500}])
        rows = [self.source(historical_period='', amount=60, collection_row_id='C'),
                self.source(historical_period='', amount=40, collection_row_id='C')]
        row = self.build(rows, {'C': collection})[0]
        self.assertEqual((row['applied_usd'], row['assigned_usd'], row['balance_usd']), (100, 40, 60))

    def test_claim_shared_with_other_import_does_not_invent_split(self):
        row = self.build([self.source(historical_period='', amount=60, collection_row_id='C')],
                         {'C': dict(applied_usd=100, remittance_detail=[{'importe_usd': 40}])})[0]
        self.assertEqual(row['applied_usd'], 60)
        self.assertIsNone(row['assigned_usd'])
        self.assertIsNone(row['balance_usd'])
        self.assertEqual(row['status'], 'Revisar')

    def test_missing_or_unreadable_collection_does_not_show_zero_debt(self):
        row = self.build([self.source(historical_period='', collection_row_id='PRIVATE')])[0]
        self.assertIsNone(row['balance_usd'])
        row = self.build([self.source(historical_period='', match_status='Sin coincidencia')])[0]
        self.assertEqual(row['balance_usd'], 100)

    def test_no_netting_between_loans_and_no_silent_truncation(self):
        rows = self.build([self.source(loan_number='L1', historical_remitted_usd=110), self.source(loan_number='L2')])
        self.assertEqual((rows[0]['balance_usd'], rows[0]['status']), (100, 'Revisar'))
        self.assertEqual(len(self.build([self.source(client_number=str(i)) for i in range(1201)])), 1201)

    def test_endpoint_checks_import_permission_before_children(self):
        document = Mock()
        document.check_permission.side_effect = PermissionError
        with patch.object(summary.frappe, 'get_doc', return_value=document), patch.object(summary.frappe, 'get_all') as read:
            with self.assertRaises(PermissionError):
                summary.get_client_summary('PRIVATE')
        read.assert_not_called()

    def test_frappe_child_document_is_not_a_subscriptable_dict(self):
        from frappe.model.base_document import BaseDocument
        with patch.object(BaseDocument, '_get_table_fields', return_value=[]):
            row = BaseDocument(self.source(doctype='CN Source Row', loan_number='L', historical_remitted_usd=30))
        result = self.build([row])[0]
        self.assertEqual(result['balance_usd'], 70)
        self.assertEqual(result['loans'], ['L'])
        with patch.object(BaseDocument, '_get_table_fields', return_value=[]):
            row = BaseDocument(self.source(doctype='CN Source Row', historical_period='', collection_row_id='C'))
        self.assertEqual(summary.collection_links(row, 100), [{'collection_row_id': 'C', 'amount_usd': 100}])

    def test_endpoint_scopes_children_and_respects_period_permission(self):
        document = frappe._dict(rows=[frappe._dict(self.source(historical_period='', collection_row_id='C'))],
            historical_period='', historical_backfill=0)
        document.check_permission = Mock()
        with patch.object(summary.frappe, 'get_doc', return_value=document), \
             patch.object(summary.frappe, 'get_all', return_value=[frappe._dict(name='C', parent='P', applied_usd=100)]) as read, \
             patch.object(summary.frappe, 'has_permission', return_value=False):
            result = summary.get_client_summary('I')
        self.assertIsNone(result['rows'][0]['balance_usd'])
        self.assertEqual(read.call_args.kwargs['filters']['name'], ['in', ['C']])
        document.check_permission.assert_called_once_with('read')
