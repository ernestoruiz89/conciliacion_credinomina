"""Exception writers and closure share cash locks before document row locks."""
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import period_lock
from credinomina_reconciliation import control_exceptions as api
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_exception import cn_reconciliation_exception as exception
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as period


class ExceptionClosureLockTests(unittest.TestCase):
    def document(self, **changes):
        doc = object.__new__(exception.CNReconciliationException)
        doc.__dict__.update(dict(doctype='CN Reconciliation Exception', name='X', period='NEW',
            flags=frappe._dict(), check_permission=Mock()), **changes)
        return doc

    def test_insert_takes_lock_for_financial_period_only(self):
        doc = self.document(period='P', origin_period='INFORMATIONAL')
        with patch.object(exception, 'lock_period_companies') as lock:
            doc.before_insert()
        lock.assert_called_once_with(['P'])

    def test_edit_locks_previous_and_current_period_before_framework_row_lock(self):
        order = []
        doc = self.document()
        with patch.object(frappe.db, 'get_value', return_value='OLD'), \
             patch.object(exception, 'lock_period_companies', side_effect=lambda periods: order.append(('pool', periods))), \
             patch.object(exception.Document, '_save', side_effect=lambda *args, **kwargs: order.append(('document', kwargs))):
            doc._save(ignore_version=True)
        self.assertEqual(order, [('pool', ['OLD', 'NEW']), ('document', {'ignore_version': True})])
        doc.check_permission.assert_called_once_with('write')

    def test_new_save_reaches_insert_without_reading_existing_exception(self):
        doc = self.document(__islocal=1)
        with patch.object(frappe.db, 'get_value') as read, patch.object(exception.Document, '_save'):
            doc._save()
        read.assert_not_called()

    def test_period_company_scope_is_batched_and_includes_authorized_payers_once(self):
        with patch.object(frappe, 'get_all', return_value=[frappe._dict(employer='A'), frappe._dict(employer='B')]) as read, \
             patch('credinomina_reconciliation.paying_employers.reconciliation_companies', side_effect=lambda owner: [owner, 'PAYER']), \
             patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool') as lock:
            period_lock.lock_period_companies(['P2', 'P1', 'P1', None], employers=['A'])
        self.assertEqual(read.call_args.kwargs['filters'], {'name': ['in', ['P1', 'P2']]})
        lock.assert_called_once_with({'A', 'B', 'PAYER'})

    def test_informational_origin_without_period_does_not_change_cash_generation(self):
        with patch.object(frappe, 'get_all') as read, \
             patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool') as lock:
            period_lock.lock_period_companies([None, ''])
        read.assert_not_called()
        lock.assert_not_called()

    def test_delete_uses_current_nonwaiting_period_read_instead_of_stale_snapshot(self):
        doc = self.document(period='P')
        with patch.object(frappe.db, 'get_value', return_value='Cerrado') as read, \
             patch.object(frappe, 'throw', side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
            doc.on_trash()
        read.assert_called_once_with('CN Reconciliation Period', 'P', 'status', for_update=True, wait=False)

    def test_closure_locks_company_even_when_no_source_reconciliation_runs(self):
        doc = frappe._dict(name='P', employer='A', status='Conciliado', check_permission=Mock())
        order = []
        with patch.object(frappe, 'get_doc', return_value=doc), \
             patch.object(period, 'lock_period_companies', side_effect=lambda **kwargs: order.append(('pool', kwargs))), \
             patch.object(period, '_reconcile_if_sources', side_effect=lambda *args, **kwargs: order.append(('reconcile', args))), \
             patch.object(period, '_pending_remittance_details_for_period', side_effect=RuntimeError('stop')):
            with self.assertRaisesRegex(RuntimeError, 'stop'):
                period.close_period('P')
        self.assertEqual(order, [('pool', {'employers': ['A']}), ('reconcile', ('A',))])

    def test_application_exception_api_locks_pool_before_period_row(self):
        order = []
        doc = frappe._dict(status='Cerrado', check_permission=Mock())
        with patch.object(frappe, 'has_permission', return_value=True), \
             patch.object(api, 'lock_period_companies', side_effect=lambda names: order.append(('pool', names))), \
             patch.object(frappe.db, 'sql', side_effect=lambda *args, **kwargs: order.append(('row', args[1]))), \
             patch.object(frappe, 'get_doc', return_value=doc), \
             patch.object(frappe, 'throw', side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
            api.create_application_exception('P', 'A', 10, 'Saldo pendiente')
        self.assertEqual(order, [('pool', ['P']), ('row', ('P',))])
