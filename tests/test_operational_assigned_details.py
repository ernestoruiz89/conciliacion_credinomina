import copy
import json
import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import operational_assigned_details as api
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report


class AssignedDetailsTests(unittest.TestCase):
    def setUp(self):
        for replacement in (patch.object(frappe, 'has_permission', return_value=True),
                            patch('credinomina_reconciliation.row_link_details.records', return_value=[frappe._dict(name='VISIBLE')])):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.row = dict(position_type='Aplicación', period='P', source_rows='A', assigned_usd=100, applied_usd=120)

    def deposit(self, entries, name='DEP'):
        return dict(name=name, docstatus=1, allocation_detail=json.dumps(entries))

    def app(self, amount, name='A'):
        return dict(tipo='Aplicacion historica', periodo='P', aplicacion_id=name, importe_usd=amount)

    def test_multiple_deposits_and_credit_funding_exclude_net_application_adjustment(self):
        deposits = [self.deposit([self.app(60)], 'D1'), self.deposit([self.app(30)], 'D2')]
        links = {'A': [dict(document='CREDIT', category='Saldo a favor del cliente', amount_usd=10),
                       dict(document='ADJUST', category='Ajuste de aplicación', amount_usd=5)]}
        before = copy.deepcopy((self.row, deposits, links))
        result = api.build_details(self.row, [], None, deposits, links)
        self.assertEqual(result['total_usd'], 100)
        self.assertEqual([row['document'] for row in result['rows']], ['D1', 'D2', 'CREDIT'])
        self.assertEqual(result['adjustments'][0]['amount_usd'], 5)
        self.assertEqual((self.row, deposits, links), before)

    def test_negative_counterpart_separates_real_cash_only_when_attribution_is_proven(self):
        offset = dict(tipo='Partida complementaria', partida='X', importe_usd=-10)
        result = api.build_details(self.row, [], None, [self.deposit([self.app(100), offset])], {})
        self.assertEqual([row['amount_usd'] for row in result['rows']], [90, 10])
        self.assertEqual(result['total_usd'], 100)
        shared = api.build_details(self.row, [], None, [self.deposit([self.app(100), self.app(50, 'OTHER'), offset])], {})
        self.assertEqual(shared['total_usd'], 100)
        self.assertIn('cobertura conjunta', shared['rows'][0]['kind'])
        self.assertIsNone(shared['adjustments'][0]['amount_usd'])
        self.assertTrue(shared['notes'])

    def test_shared_collection_is_counted_once_and_fifo_filters_source_portions(self):
        collection = dict(name='COL', parent='P', row_key='KEY', applied_usd=120)
        row = dict(self.row, source_rows='A, B', collection_row_id='COL')
        entry = dict(tipo='Cobranza', periodo='P', fila_id='KEY', importe_usd=100)
        self.assertEqual(api.build_details(row, [], collection, [self.deposit([entry])], {})['total_usd'], 100)
        partial = dict(row, source_rows='A', applied_usd=60, assigned_usd=50)
        result = api.build_details(partial, [], collection, [self.deposit([entry])], {})
        self.assertIsNone(result['total_usd'])
        entry['aplicaciones_fifo'] = [dict(application_id='A', amount_usd=50), dict(application_id='B', amount_usd=50)]
        result = api.build_details(partial, [], collection, [self.deposit([entry])], {})
        self.assertEqual(result['total_usd'], 50)

    def test_consolidated_cancelled_and_draft_deposits_do_not_duplicate_cash(self):
        deposits = [self.deposit([self.app(100)])]
        for fields in (dict(docstatus=0), dict(docstatus=2), dict(cash_identity_status='Origen vinculado'), dict(consolidated_deposit='DEP')):
            deposits.append(dict(self.deposit([self.app(100)], str(fields)), **fields))
        result = api.build_details(self.row, [], None, deposits, {})
        self.assertEqual(result['total_usd'], 100)
        self.assertEqual(len(result['rows']), 1)

    def test_tolerance_separates_consumed_bank_cash_from_signed_difference(self):
        item_links = {'A': [dict(document='TOL', category='Diferencia por tolerancia', amount_usd=-.01)]}
        deposit = self.deposit([self.app(100), dict(tipo='Movimiento de conciliación', movimiento='TOL', importe_usd=.01)])
        result = api.build_details(self.row, [], None, [deposit], item_links)
        self.assertEqual([entry['amount_usd'] for entry in result['rows']], [100.01, -.01])
        self.assertEqual(result['total_usd'], 100)
        collection = dict(name='COL', applied_usd=120)
        links = {}
        api._collection_movements(self.row, collection, [dict(name='TOL', docstatus=1, category='Diferencia por tolerancia',
            status='Vigente', claim_id='C:COL', signed_amount_usd=.01)], links)
        self.assertEqual(links, item_links)

    def test_restricted_links_never_expose_document_names(self):
        with patch('credinomina_reconciliation.row_link_details.records', return_value=[]):
            result = api.build_details(self.row, [], None, [self.deposit([self.app(100)], 'SECRET')], {})
        self.assertNotIn('SECRET', json.dumps(result))

    def test_cutoff_credit_history_excludes_later_uses_and_later_reversals(self):
        item = dict(name='C', docstatus=1, category='Saldo a favor del cliente', credit_history=json.dumps([
            dict(operation_id='USE', application_id='A', importe_usd=10, fecha='2026-08-01'),
            dict(operation_id='LATE', application_id='A', importe_usd=20, fecha='2026-09-01'),
            dict(operation_id='UNDO', reverses='USE', importe_usd=-10, fecha='2026-10-01')]))
        result = api._cutoff_item_links([item], [dict(name='A')], '2026-08-31')
        self.assertEqual(sum(row['amount_usd'] for row in result['A']), 10)

    def test_core_metadata_preserves_multiple_origins_dates_and_permission_scope(self):
        from credinomina_reconciliation import accounting_related_records as related
        result = dict(rows=[dict(doctype='CN Remittance Allocation', document='DEP', amount_usd=100),
            dict(doctype='', document='', amount_usd=5)], adjustments=[
                dict(doctype='CN Complementary Item', document='ITEM', amount_usd=2)])
        origins = [frappe._dict(source_date='2026-08-15', source_voucher='00123', accounting_reference='REF1', source_description='Origen uno'),
                   frappe._dict(source_date='2026-08-16', source_voucher='00456', accounting_reference='REF2', source_description='Origen dos')]
        def documents(doctype, **kwargs):
            self.assertNotIn('', kwargs['filters']['name'][1])
            if doctype == 'CN Remittance Allocation':
                return [frappe._dict(name='DEP', deposit_date='2026-08-14', source_voucher='HEADER')]
            return [frappe._dict(name='ITEM', posting_date='2026-08-20', source_date='2026-08-17',
                source_voucher='00789', accounting_reference='COMP-REF', source_description='Complementaria')]
        def attach(documents, **kwargs):
            self.assertEqual([doc.name for doc in documents], ['DEP'])
            self.assertIn('source_date', kwargs['fields'])
            documents[0].accounting_sources = [*origins, origins[0]]
        with patch.object(related, 'records', side_effect=documents), patch.object(related, 'attach_sources', side_effect=attach), \
                patch.object(frappe.db, 'set_value') as write:
            api._attach_core_entries(result)
            write.assert_not_called()
        entries = result['rows'][0]['core_entries']
        self.assertEqual([(entry['date'], entry['voucher']) for entry in entries], [('2026-08-15', '00123'), ('2026-08-16', '00456')])
        self.assertEqual(result['rows'][0]['amount_usd'], 100)
        self.assertNotIn('core_entries', result['rows'][1])
        self.assertEqual(result['adjustments'][0]['core_entries'], [dict(date='2026-08-17', voucher='00789', reference='COMP-REF', description='Complementaria')])
        with patch.object(frappe, 'has_permission', return_value=False), patch.object(related, 'records') as read:
            api._attach_core_entries(dict(rows=[dict(doctype='CN Remittance Allocation', document='DENIED')], adjustments=[]))
            read.assert_not_called()

    def test_missing_core_date_uses_document_date_without_inventing_posting_metadata(self):
        from credinomina_reconciliation import accounting_related_records as related
        with patch.object(related, 'records', return_value=[frappe._dict(name='DEP', deposit_date='2026-08-01')]), \
             patch.object(related, 'attach_sources'):
            result = related._document_core_entries('CN Remittance Allocation', [dict(document='DEP')], [], '', include_date=True)
        self.assertEqual(result['DEP'], [dict(date='2026-08-01', voucher=None, reference=None, description=None)])

    def test_endpoint_checks_report_visible_row_and_uses_projection_without_current_links(self):
        snapshot = dict(cutoff_date='2026-08-31', position_context=dict(
            sources=[dict(name='A')], collections={}, deposits=[self.deposit([self.app(100)])], items=[]))
        filters = frappe._dict(cutoff_date='2026-08-31')
        with patch('frappe.desk.query_report.get_report_doc') as permission, \
             patch.object(report, 'load_report_rows', return_value=(filters, [], [self.row], snapshot)), \
             patch('credinomina_reconciliation.accounting_related_records._document_core_entries',
                   return_value={'DEP': [dict(date='2026-08-01', voucher='00123', reference='REF', description='Core')]}), \
             patch.object(api, '_current_evidence') as current, patch.object(frappe.db, 'set_value') as write:
            result = api.get_details('A', 'P', filters=json.dumps(filters))
            self.assertEqual(result['total_usd'], 100)
            self.assertEqual(result['cutoff_date'], '2026-08-31')
            self.assertEqual(result['rows'][0]['core_entries'][0]['voucher'], '00123')
            permission.assert_called_with('Estado de Cuenta Operativo')
            current.assert_not_called()
            write.assert_not_called()
            for names, period in [('SECRET', 'P'), ('A', 'OTHER')]:
                with self.assertRaises(frappe.ValidationError):
                    api.get_details(names, period, filters=filters)
        with patch('frappe.desk.query_report.get_report_doc', side_effect=frappe.PermissionError):
            with self.assertRaises(frappe.PermissionError):
                api.get_details('A', 'P')
