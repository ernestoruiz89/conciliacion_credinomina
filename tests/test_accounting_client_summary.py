import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import accounting_client_summary as summary


class AccountingClientSummaryTests(unittest.TestCase):
    def test_explicit_fifo_slices_attribute_shared_cash_to_the_correct_application(self):
        collection = dict(applied_usd=200, remittance_detail=[dict(importe_usd=100,
            aplicaciones_fifo=[dict(application_id='B', amount_usd=100)])])
        sources = [self.source(name=name, historical_period='', collection_row_id='C') for name in ('A', 'B')]
        rows = summary.build_summary({'rows': sources}, {'C': collection}, group_by_row=True)
        by_name = {row['source_row_name']: row for row in rows}
        self.assertEqual((by_name['A']['assigned_usd'], by_name['A']['balance_usd']), (0, 100))
        self.assertEqual((by_name['B']['assigned_usd'], by_name['B']['balance_usd']), (100, 0))
        self.assertEqual(self.build(sources, {'C': collection})[0]['assigned_usd'], 100)
        from credinomina_reconciliation.company_transactions import application_progress_index, application_month_amounts
        sources[0].update(parent='I', event_date='2026-09-30')
        sources[1].update(parent='I', event_date='2026-10-15')
        parents = {'I': dict(status='Importado', employer='E')}
        progress = application_progress_index(sources, parents, {'C': collection})
        self.assertEqual((progress['A']['state'], progress['B']['state']), ('Pendiente', 'Conciliado'))
        months = application_month_amounts(sources, parents, {'C': collection}, progress)
        self.assertEqual(months[('E', '2026-09')]['covered_usd'], 0)
        self.assertEqual(months[('E', '2026-10')]['covered_usd'], 100)
        # A duplicated link to the same claim must not spend the slice twice.
        sources[1]['application_allocation_detail'] = [dict(collection_row_id='C', amount_usd=50)] * 2
        self.assertEqual(summary.row_amounts({'rows': sources}, {'C': collection})['B']['applied_usd'], 100)
        # Mixed unsplit evidence remains unknown, rather than guessing a share.
        collection['remittance_detail'].append(dict(importe_usd=10))
        self.assertIsNone(summary.row_amounts({'rows': sources}, {'C': collection})['B']['balance_usd'])

    def test_invalid_fifo_slice_does_not_produce_a_paid_application(self):
        source = self.source(historical_period='', collection_row_id='C')
        collection = dict(applied_usd=100, remittance_detail=[dict(importe_usd=50,
            aplicaciones_fifo=[dict(application_id='A', amount_usd=100)])])
        with self.assertRaises(ValueError):
            self.build([source], {'C': collection})

    def test_projected_direct_cash_cannot_pay_a_legacy_application_twice(self):
        source = self.source(historical_period='', collection_row_id='C')
        collection = dict(applied_usd=200, remittance_detail=[dict(importe_usd=100, aplicacion_directa='OTHER')])
        row = self.build([source], {'C': collection})[0]
        self.assertEqual((row['assigned_usd'], row['balance_usd']), (0, 100))

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

    def test_client_statement_uses_original_amount_and_signed_complementary_coverage(self):
        sources = [self.source(application_adjustment_usd=20, historical_remitted_usd=30)]
        document = {'rows': sources}
        base = self.build(sources)
        row = summary.client_statement_columns(document, base)[0]
        self.assertEqual([row[key] for key in ('amount_usd', 'deposits_usd', 'complementary_usd', 'balance_usd')],
                         [100, 30, 20, 50])
        self.assertEqual(base[0]['applied_usd'], 80)
        self.assertNotIn('amount_usd', base[0], 'Presentation must not mutate balances used by reconciliation')
        for delta, cash in ((.01, 100.01), (-.01, 99.99)):
            sources = [self.source(historical_remitted_usd=cash, historical_detail=[{'diferencia_usd': delta}])]
            row = summary.client_statement_columns({'rows': sources}, self.build(sources))[0]
            self.assertEqual(row['complementary_usd'], -delta)
            self.assertAlmostEqual(row['amount_usd'] - row['deposits_usd'] - row['complementary_usd'], row['balance_usd'])

    def test_customer_credit_funding_is_complementary_not_new_bank_cash(self):
        sources = [self.source(application_adjustment_usd=20, historical_remitted_usd=60,
            historical_detail=[{'importe_usd':10, 'saldo_a_favor':'CREDIT'}])]
        row = summary.client_statement_columns({'rows': sources}, self.build(sources))[0]
        self.assertEqual([row[key] for key in ('amount_usd', 'deposits_usd', 'complementary_usd', 'balance_usd')],
                         [100, 50, 30, 20])
        totals = summary.balance_totals({'rows':sources}, self.build(sources))
        self.assertEqual([totals[key] for key in ('amount_usd', 'deposits_usd', 'complementary_usd', 'balance_usd')],
                         [100, 50, 30, 20])

    def test_client_statement_groups_all_credit_movements_once(self):
        sources = [self.source(name='A', amount=100, application_adjustment_usd=20, historical_remitted_usd=10),
            self.source(name='B', amount=50, application_adjustment_usd=5, historical_remitted_usd=5),
            self.source(name='C', client_number='OTHER', amount=30, historical_remitted_usd=10)]
        rows = summary.client_statement_columns({'rows':sources}, self.build(sources))
        self.assertEqual({row['client_number']: tuple(row[key] for key in ('amount_usd', 'deposits_usd', 'complementary_usd', 'balance_usd'))
            for row in rows}, {'1':(150,15,25,110), 'OTHER':(30,10,0,20)})

    def test_credit_funding_is_attributed_by_executed_fifo_and_ignores_stale_journals(self):
        sources = [self.source(name=name, client_number=name, historical_period='', collection_row_id='C')
                   for name in ('A', 'B')]
        collection = dict(applied_usd=200, remittance_detail=[
            dict(importe_usd=40, aplicaciones_fifo=[dict(application_id='A', amount_usd=40)]),
            dict(importe_usd=30, saldo_a_favor='CREDIT', aplicaciones_fifo=[dict(application_id='B', amount_usd=30)])])
        rows = summary.client_statement_columns({'rows':sources}, self.build(sources, {'C':collection}))
        self.assertEqual({row['client_number']: (row['deposits_usd'], row['complementary_usd'], row['balance_usd'])
                          for row in rows}, {'A':(40,0,60), 'B':(0,30,70)})
        stale = self.source(match_status='Sin coincidencia', historical_detail=[dict(importe_usd=10, saldo_a_favor='STALE')])
        ignored = self.source(name='IGNORED', match_status='Ignorado', application_adjustment_usd=20)
        row = summary.client_statement_columns({'rows':[stale,ignored]}, self.build([stale,ignored]))[0]
        self.assertEqual((row['amount_usd'], row['deposits_usd'], row['complementary_usd'], row['balance_usd']), (100,0,0,100))

    def test_client_statement_retains_unknown_shared_collection_cash(self):
        sources = [self.source(historical_period='', collection_row_id='C', amount=60)]
        rows = self.build(sources, {'C': dict(applied_usd=100, remittance_detail=[{'importe_usd':40}])})
        row = summary.client_statement_columns({'rows':sources}, rows)[0]
        self.assertEqual(row['amount_usd'], 60)
        self.assertIsNone(row['deposits_usd'])
        self.assertIsNone(row['complementary_usd'])

    def test_company_difference_is_not_divided_between_clients(self):
        proof = dict(deposit='DEP', period='P', amount_usd=-.01, application_ids=['A','B'])
        sources = [self.source(name=name, amount=amount, client_number=name, historical_remitted_usd=amount,
            historical_detail=[dict(importe_usd=amount, conversion_empresa=proof)]) for name,amount in [('A',60),('B',40)]]
        rows = summary.client_statement_columns({'rows':sources}, self.build(sources))
        self.assertTrue(all(row['deposits_usd'] is None and row['complementary_usd'] == 0 for row in rows))
        self.assertTrue(all('compartida' in row['observations'][-1] for row in rows))

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
        with patch.object(BaseDocument, '_get_table_fields', return_value=[]), \
             patch.object(frappe, 'get_meta', return_value=frappe._dict(_table_doctypes={})):
            row = BaseDocument(self.source(doctype='CN Source Row', loan_number='L', historical_remitted_usd=30))
        result = self.build([row])[0]
        self.assertEqual(result['balance_usd'], 70)
        self.assertEqual(result['loans'], ['L'])
        with patch.object(BaseDocument, '_get_table_fields', return_value=[]), \
             patch.object(frappe, 'get_meta', return_value=frappe._dict(_table_doctypes={})):
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
        self.assertIsNone(result['row_amounts']['A']['balance_usd'])
        self.assertEqual(read.call_args.kwargs['filters']['name'], ['in', ['C']])
        document.check_permission.assert_called_once_with('read')

    def test_row_amounts_include_confirmed_adjustments_and_real_cash(self):
        rows = [self.source(name='PARTIAL', application_adjustment_usd=20, historical_remitted_usd=30),
                self.source(name='ADJUSTED', application_adjustment_usd=100),
                self.source(name='UNASSIGNED', historical_period='', match_status='Sin coincidencia')]
        original = [dict(row) for row in rows]
        amounts = summary.row_amounts({'rows': rows}, {})
        self.assertEqual((amounts['PARTIAL']['applied_usd'], amounts['PARTIAL']['balance_usd']), (50, 50))
        self.assertEqual((amounts['ADJUSTED']['applied_usd'], amounts['ADJUSTED']['balance_usd']), (100, 0))
        self.assertEqual((amounts['UNASSIGNED']['applied_usd'], amounts['UNASSIGNED']['balance_usd']), (0, 100))
        self.assertEqual(rows, original)

    def test_row_amounts_do_not_invent_a_split_within_the_same_customer(self):
        rows = [self.source(name='A', historical_period='', amount=60, collection_row_id='C'),
                self.source(name='B', historical_period='', amount=40, collection_row_id='C')]
        collections = {'C': dict(applied_usd=100, remittance_detail=[{'importe_usd': 40}])}
        self.assertEqual(self.build(rows, collections)[0]['assigned_usd'], 40)
        amounts = summary.row_amounts({'rows': rows}, collections)
        for row in amounts.values():
            self.assertIsNone(row['applied_usd'])
            self.assertIsNone(row['balance_usd'])
            self.assertIn('compartida', row['issue'])

    def test_single_operational_row_has_attributable_coverage(self):
        row = self.source(historical_period='', collection_row_id='C', application_adjustment_usd=20)
        collections = {'C': dict(applied_usd=80, remittance_detail=[{'importe_usd': 30},
            {'importe_usd': 999, 'destino': 'Partida complementaria'}])}
        amounts = summary.row_amounts({'rows': [row]}, collections)['A']
        self.assertEqual((amounts['applied_usd'], amounts['balance_usd']), (50, 50))

    def test_minor_differences_do_not_inflate_applied_and_overpayment_is_visible(self):
        for gross, cash, delta in ((46.52, 46.53, .01), (46.53, 46.52, -.01)):
            with self.subTest(delta=delta):
                row = self.source(amount=gross, historical_remitted_usd=cash, historical_detail=[{'diferencia_usd': delta}])
                result = summary.row_amounts({'rows': [row]}, {})['A']
                self.assertEqual((result['applied_usd'], result['balance_usd']), (gross, 0))
        result = summary.row_amounts({'rows': [self.source(historical_remitted_usd=110)]}, {})['A']
        self.assertEqual((result['applied_usd'], result['balance_usd']), (110, 0))
        self.assertIn('mayor', result['issue'])

    def test_zero_unknown_excluded_rows_and_cancellation(self):
        rows = [self.source(name='ZERO', amount=0), self.source(name='NO-FX', currency='NIO'),
                self.source(name='IGNORED', match_status='Ignorado'), self.source(name='INACTIVE', effective=0),
                self.source(name='DEPOSIT', event_type='Deposito')]
        result = summary.row_amounts({'rows': rows}, {})
        self.assertEqual(set(result), {'ZERO', 'NO-FX'})
        self.assertEqual((result['ZERO']['applied_usd'], result['ZERO']['balance_usd']), (0, 0))
        self.assertIsNone(result['NO-FX']['applied_usd'])
        self.assertIsNone(result['NO-FX']['balance_usd'])
        self.assertEqual(summary.row_amounts({'rows': rows, 'docstatus': 2}, {}), {})
