import unittest
from unittest.mock import Mock, patch

import frappe
from credinomina_reconciliation import period_quick_entry as api


class PeriodQuickEntryTests(unittest.TestCase):
    def mock_documents(self, period):
        def get_doc(doctype, *_args):
            if isinstance(doctype, dict):
                period.configure_mock(**doctype)
                period.get.return_value = None
                return period
            return Mock()
        return patch.object(api.frappe, 'get_doc', side_effect=get_doc)

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

    def test_header_and_row_links_are_available_when_loading_payroll(self):
        for row_link in (False, True):
            with self.subTest(row_link=row_link):
                current = frappe._dict(name='ROW', historical_period='')
                other = frappe._dict(name='KEEP', historical_period='OTHER-PERIOD')
                source = frappe._dict(name='I', historical_period='', rows=[current, other],
                                      save=Mock(), _validate_historical_periods=Mock())
                period = Mock()
                period.name = 'P'
                period.insert.return_value = period
                def imported(name, **kwargs):
                    self.assertEqual(name, 'P')
                    if row_link:
                        source.save.assert_called_once_with()
                        self.assertEqual(current.historical_period, 'P')
                        self.assertEqual(source.historical_period, '')
                    else:
                        source.save.assert_not_called()
                        source._validate_historical_periods.assert_called_once_with()
                        self.assertEqual(source.historical_period, 'P')
                        self.assertEqual(current.historical_period, '')
                    self.assertEqual(other.historical_period, 'OTHER-PERIOD')
                    return {'source_reconciliation':{}}
                with patch.object(api.frappe, 'has_permission'), self.mock_documents(period), \
                     patch.object(api, '_source_link', return_value=(source, current if row_link else None)), \
                     patch.object(api.api, 'import_collection', side_effect=imported), \
                     patch.object(api.api, '_import_collection', side_effect=imported) as collection, \
                     patch.object(api.accounting, '_load_source_movements', return_value={'rows':1}) as core, \
                     patch.object(api.api, '_first_reconciliation_result', return_value={}):
                    result = api.create({'reconciliation_mode':'Operativa', 'collection_file':'payroll.csv'}, {'name':'I'})
                self.assertEqual(result['source_import'], 'I')
                self.assertEqual(result['movements_loaded'], not row_link)
                if row_link:
                    core.assert_not_called()
                    collection.assert_not_called()
                else:
                    collection.assert_called_once_with('P', reconcile=False)
                    core.assert_called_once_with(source)
                    source.save.assert_not_called()

    def test_historical_and_operative_without_payroll_load_core_with_the_locked_source(self):
        for mode in ('Historica', 'Operativa'):
            with self.subTest(mode=mode):
                other = frappe._dict(historical_period='KEEP')
                source = frappe._dict(name='I', historical_period='', rows=[other],
                                      save=Mock(), _validate_historical_periods=Mock())
                period = Mock()
                period.name = 'P'
                period.insert.return_value = period
                def load(locked_source):
                    self.assertIs(locked_source, source)
                    source.save.assert_not_called()
                    source._validate_historical_periods.assert_called_once_with()
                    self.assertEqual(source.historical_period, 'P')
                    self.assertEqual(other.historical_period, 'KEEP')
                    return {'rows':3, 'import_name':'DATED-IMPORT'}
                with patch.object(api.frappe, 'has_permission'), self.mock_documents(period), \
                     patch.object(api, '_source_link', return_value=(source, None)), \
                     patch.object(api.accounting, '_load_source_movements', side_effect=load) as core, \
                     patch.object(api.api, 'import_collection') as collection:
                    result = api.create({'reconciliation_mode':mode, 'remark':'Observación del período'}, {'name':'I'})
                self.assertEqual(period.remark, 'Observación del período')
                self.assertEqual(result['source_import'], 'DATED-IMPORT')
                self.assertTrue(result['movements_loaded'])
                self.assertEqual(result['movement_rows'], 3)
                self.assertFalse(result['collection_loaded'])
                core.assert_called_once_with(source)
                collection.assert_not_called()

    def test_invalid_existing_rows_stop_creation_before_loading_payroll_or_core(self):
        def reject_binding():
            self.assertEqual(source.historical_period, 'P')
            raise frappe.ValidationError('El período no corresponde a la fila.')

        source = frappe._dict(name='I', historical_period='', rows=[frappe._dict(name='ROW')], save=Mock(),
                              _validate_historical_periods=Mock(side_effect=reject_binding))
        period = Mock()
        period.name = 'P'
        with patch.object(api.frappe, 'has_permission'), self.mock_documents(period), \
             patch.object(api, '_source_link', return_value=(source, None)), \
             patch.object(api.api, '_import_collection') as collection, \
             patch.object(api.accounting, '_load_source_movements') as core:
            with self.assertRaisesRegex(frappe.ValidationError, 'no corresponde'):
                api.create({'reconciliation_mode':'Operativa', 'collection_file':'payroll.csv'}, {'name':'I'})
        source.save.assert_not_called()
        collection.assert_not_called()
        core.assert_not_called()
        period.as_dict.assert_not_called()

    def test_nested_core_failure_never_retries_or_rolls_back_the_middle_of_creation(self):
        source = frappe._dict(name='I', historical_period='', rows=[], save=Mock())
        period = Mock()
        period.name = 'P'
        period.insert.return_value = period
        with patch.object(api.frappe, 'has_permission'), self.mock_documents(period), \
             patch.object(api, '_source_link', return_value=(source, None)), \
             patch.object(api.accounting, '_load_source_movements', side_effect=frappe.QueryDeadlockError) as core, \
             patch.object(api.accounting, 'import_source_file') as standalone, \
             patch.object(api.frappe.db, 'rollback') as rollback:
            with self.assertRaises(frappe.QueryDeadlockError):
                api.create({'reconciliation_mode':'Historica'}, {'name':'I'})
        core.assert_called_once_with(source)
        source.save.assert_not_called()
        standalone.assert_not_called()
        rollback.assert_not_called()  # The complete HTTP request handles rollback.

    def test_only_operative_with_payroll_runs_once_and_preserves_normal_permissions(self):
        for mode, file in [('Operativa', '/private/files/payroll.csv'), ('Operativa', ''), ('Historica', '')]:
            with self.subTest(mode=mode, file=file):
                document = Mock(name='period')
                document.name = 'P'
                document.insert.return_value = document
                document.as_dict.return_value = {'name':'P'}
                with patch.object(api.frappe, 'has_permission') as permission, \
                     self.mock_documents(document) as get_doc, \
                     patch.object(api.api, 'import_collection', return_value={'source_reconciliation':{'period':'P'}}) as load, \
                     patch.object(api.api, '_first_reconciliation_result', return_value={'reviewed':1}) as summarize:
                    result = api.create({'employer':'E', 'payroll_month':'2026-09-01', 'reconciliation_mode':mode,
                                         'collection_file':file, 'name':'INJECTED', 'expected_usd':999, 'collection_rows':[{}]})
                fields = get_doc.call_args_list[0].args[0]
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
        with patch.object(api.frappe, 'has_permission'), self.mock_documents(document), \
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
        self.assertEqual(get_doc.call_count, 1)  # Build the in-memory period, with no insertion.
        get_doc.return_value.insert.assert_not_called()
        document = Mock()
        document.insert.return_value = document
        with patch.object(api.frappe, 'has_permission'), self.mock_documents(document), \
             patch.object(api.api, 'import_collection', side_effect=frappe.ValidationError), \
             patch.object(api.api, '_first_reconciliation_result') as summarize:
            with self.assertRaises(frappe.ValidationError):
                api.create({'reconciliation_mode':'Operativa', 'collection_file':'payroll.csv'})
        document.as_dict.assert_not_called()
        summarize.assert_not_called()
