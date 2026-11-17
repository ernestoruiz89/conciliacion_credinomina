import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import period_quick_entry as api


class PeriodQuickEntryTests(unittest.TestCase):
    def test_source_link_checks_version_company_existing_assignments_and_row_ownership(self):
        row = frappe._dict(name='ROW', event_type='Aplicacion', historical_period='')
        source = frappe._dict(name='I', employer='E', docstatus=0, modified='v1', historical_period='',
                             rows=[row], check_permission=Mock())
        with patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool'), \
             patch.object(api.frappe, 'get_doc', return_value=source):
            linked, selected = api._source_link({'name':'I', 'modified':'v1'}, 'E')
            self.assertIs(linked, source)
            self.assertIsNone(selected)
            self.assertIs(api._source_link({'name':'I', 'modified':'v1', 'row_name':'ROW'}, 'E')[1], row)
            for link, employer in [({'name':'I', 'modified':'old'}, 'E'),
                    ({'name':'I', 'modified':'v1'}, 'OTHER'),
                    ({'name':'I', 'modified':'v1', 'row_name':'FOREIGN'}, 'E')]:
                with self.assertRaises(frappe.ValidationError):
                    api._source_link(link, employer)
            source.historical_period = 'EXISTING'
            with self.assertRaises(frappe.ValidationError):
                api._source_link({'name':'I', 'modified':'v1'}, 'E')
            source.historical_period = ''
            row.historical_period = 'EXISTING'
            with self.assertRaises(frappe.ValidationError):
                api._source_link({'name':'I', 'modified':'v1', 'row_name':'ROW'}, 'E')
        self.assertEqual(row.historical_period, 'EXISTING')
        source.check_permission.assert_called_with('write')

    def test_binding_saves_only_selected_blank_link_before_loading(self):
        for row_link in (False, True):
            with self.subTest(row_link=row_link):
                current = frappe._dict(name='ROW', historical_period='')
                other = frappe._dict(name='KEEP', historical_period='OTHER-PERIOD')
                source = frappe._dict(name='I', historical_period='', rows=[current, other], save=Mock())
                period = Mock()
                period.name = 'P'
                period.insert.return_value = period
                def imported(name):
                    self.assertEqual(name, 'P')
                    source.save.assert_called_once_with()
                    self.assertEqual(current.historical_period if row_link else source.historical_period, 'P')
                    self.assertEqual(other.historical_period, 'OTHER-PERIOD')
                    return {'source_reconciliation':{}}
                with patch.object(api.frappe, 'has_permission'), patch.object(api.frappe, 'get_doc', return_value=period), \
                     patch.object(api, '_source_link', return_value=(source, current if row_link else None)), \
                     patch.object(api.api, 'import_collection', side_effect=imported), \
                     patch.object(api.api, '_first_reconciliation_result', return_value={}):
                    result = api.create({'reconciliation_mode':'Operativa', 'collection_file':'payroll.csv'}, {'name':'I'})
                self.assertEqual(result['source_import'], 'I')

    def test_only_operative_with_payroll_runs_once_and_preserves_normal_permissions(self):
        for mode, file in [('Operativa', '/private/files/payroll.csv'), ('Operativa', ''), ('Historica', '')]:
            with self.subTest(mode=mode, file=file):
                document = Mock(name='period')
                document.name = 'P'
                document.insert.return_value = document
                document.as_dict.return_value = {'name':'P'}
                with patch.object(api.frappe, 'has_permission') as permission, \
                     patch.object(api.frappe, 'get_doc', return_value=document) as get_doc, \
                     patch.object(api.api, 'import_collection', return_value={'source_reconciliation':{'period':'P'}}) as load, \
                     patch.object(api.api, '_first_reconciliation_result', return_value={'reviewed':1}) as summarize:
                    result = api.create({'employer':'E', 'payroll_month':'2026-09-01', 'reconciliation_mode':mode,
                                         'collection_file':file, 'name':'INJECTED', 'expected_usd':999, 'collection_rows':[{}]})
                fields = get_doc.call_args.args[0]
                self.assertNotIn('name', fields)
                self.assertNotIn('collection_rows', fields)
                self.assertNotIn('expected_usd', fields)
                document.insert.assert_called_once_with()
                permission.assert_any_call('CN Reconciliation Period', 'create', throw=True)
                if mode == 'Operativa' and file:
                    load.assert_called_once_with('P')
                    summarize.assert_called_once_with(document, {'period':'P'})
                    permission.assert_any_call('CN Accounting Import', 'write', throw=True)
                    self.assertEqual(result['reconciliation'], {'reviewed':1})
                else:
                    load.assert_not_called()
                    summarize.assert_not_called()
                self.assertEqual(result['collection_loaded'], bool(mode == 'Operativa' and file))

    def test_no_core_does_not_claim_successful_reconciliation(self):
        document = Mock()
        document.insert.return_value = document
        with patch.object(api.frappe, 'has_permission'), patch.object(api.frappe, 'get_doc', return_value=document), \
             patch.object(api.api, 'import_collection', return_value={'source_reconciliation':None}), \
             patch.object(api.api, '_first_reconciliation_result') as summarize:
            result = api.create({'reconciliation_mode':'Operativa', 'collection_file':'payroll.csv'})
        self.assertTrue(result['collection_loaded'])
        self.assertIsNone(result['reconciliation'])
        summarize.assert_not_called()

    def test_permission_and_import_errors_propagate_before_success(self):
        with patch.object(api.frappe, 'has_permission', side_effect=frappe.PermissionError), \
             patch.object(api.frappe, 'get_doc') as get_doc:
            with self.assertRaises(frappe.PermissionError):
                api.create({'reconciliation_mode':'Operativa', 'collection_file':'payroll.csv'})
        get_doc.assert_not_called()
        document = Mock()
        document.insert.return_value = document
        with patch.object(api.frappe, 'has_permission'), patch.object(api.frappe, 'get_doc', return_value=document), \
             patch.object(api.api, 'import_collection', side_effect=frappe.ValidationError), \
             patch.object(api.api, '_first_reconciliation_result') as summarize:
            with self.assertRaises(frappe.ValidationError):
                api.create({'reconciliation_mode':'Operativa', 'collection_file':'payroll.csv'})
        document.as_dict.assert_not_called()
        summarize.assert_not_called()
