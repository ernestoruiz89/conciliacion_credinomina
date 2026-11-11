"""Batched validation must keep target guards and refresh evidence on every call."""
import copy
import json
import unittest
from collections import Counter
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import remittance_validation as validation
from credinomina_reconciliation import deposit_reconciliation as scoped
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation import cn_remittance_allocation as controller


class RemittanceValidationTests(unittest.TestCase):
    @staticmethod
    def reject(message, *args, **kwargs):
        raise frappe.ValidationError(message)

    def setUp(self):
        self.rows = {
            'CN Source Row': [frappe._dict(name='APP', event_type='Aplicacion', historical_period='P',
                currency='USD', effective=1)],
            'CN Complementary Item': [frappe._dict(name='X', docstatus=1, period='P', amount_usd=-10,
                category='Ajuste de conciliación', employer='E', generic_distribution=0)],
            'CN Reconciliation Period': [frappe._dict(name='P', employer='E', status='Parcial',
                applied_usd=100, reconciliation_mode='Operativa',
                collection_rows=[frappe._dict(row_key='ROW')])],
        }
        self.queries = []

    def get_all(self, doctype, **kwargs):
        self.queries.append((doctype, kwargs))
        names = kwargs['filters']['name'][1]
        return [copy.deepcopy(row) for row in self.rows.get(doctype, []) if row.name in names]

    def get_doc(self, doctype, name):
        row = next((row for row in self.rows[doctype] if row.name == name), None)
        if row is None:
            raise frappe.DoesNotExistError(name)
        return copy.deepcopy(row)

    def get_value(self, doctype, name, fields, as_dict=False):
        row = next((row for row in self.rows.get(doctype, []) if row.name == name), None)
        if row is None:
            return None
        return frappe._dict({field: row.get(field) for field in fields}) if as_dict else row.get(fields)

    def context(self, targets, detail_periods=()):
        with patch.object(frappe, 'get_all', side_effect=self.get_all):
            return validation.TargetValidationContext(targets, detail_periods)

    def validate_target(self, target, batched):
        with patch.object(frappe.db, 'get_value', side_effect=self.get_value), \
                patch.object(frappe, 'get_doc', side_effect=self.get_doc), \
                patch.object(frappe, 'throw', side_effect=self.reject):
            context = self.context([target]) if batched else None
            controller.CNRemittanceAllocation._validate_target(target, context=context)

    def test_large_target_list_uses_bounded_reads_not_one_query_per_target(self):
        self.rows['CN Source Row'] = [frappe._dict(self.rows['CN Source Row'][0], name=f'APP{i}')
                                      for i in range(1201)]
        targets = [frappe._dict(historical_application=row.name, amount_usd=5)
                   for row in self.rows['CN Source Row']]
        context = self.context(targets * 2, [frappe._dict(period='P')])
        with patch.object(frappe.db, 'get_value') as individual:
            for target in targets:
                controller.CNRemittanceAllocation._validate_target(target, context=context)
        individual.assert_not_called()
        self.assertEqual(Counter(dt for dt, _kw in self.queries),
                         {'CN Source Row': 3, 'CN Reconciliation Period': 1})
        self.assertTrue(all(len(kw['filters']['name'][1]) <= 500 for _dt, kw in self.queries))
        self.assertTrue(all(kw['limit_page_length'] == 0 for _dt, kw in self.queries))

    def test_batch_and_individual_validation_accept_the_same_targets(self):
        for target in (frappe._dict(historical_application=' APP ', amount_usd=50),
                       frappe._dict(complementary_item='X', amount_usd=-5),
                       frappe._dict(period='P', row_key=' ROW ', amount_usd=50)):
            with self.subTest(target=target):
                individual, batched = copy.deepcopy(target), copy.deepcopy(target)
                self.validate_target(individual, False)
                self.validate_target(batched, True)
                self.assertEqual(individual, batched)

    def test_all_target_rejections_match_individual_validation(self):
        cases = [
            ('CN Source Row', {'effective': 0}, {'historical_application': 'APP', 'amount_usd': 5}),
            ('CN Source Row', {'currency': 'NIO'}, {'historical_application': 'APP', 'amount_usd': 5}),
            ('CN Source Row', {'event_type': 'Deposito'}, {'historical_application': 'APP', 'amount_usd': 5}),
            ('CN Source Row', {'historical_period': ''}, {'historical_application': 'APP', 'amount_usd': 5}),
            ('CN Reconciliation Period', {'status': 'Cerrado'}, {'historical_application': 'APP', 'amount_usd': 5}),
            ('CN Reconciliation Period', {'status': 'Cerrado'}, {'complementary_item': 'X', 'amount_usd': -5}),
            ('CN Reconciliation Period', {'status': 'Cerrado'}, {'period': 'P', 'row_key': 'ROW', 'amount_usd': 5}),
            ('CN Reconciliation Period', {'reconciliation_mode': 'Historica'}, {'period': 'P', 'row_key': 'ROW', 'amount_usd': 5}),
            ('CN Reconciliation Period', {'collection_rows': [frappe._dict(row_key='ROW')] * 2},
             {'period': 'P', 'row_key': 'ROW', 'amount_usd': 5}),
            ('CN Complementary Item', {'docstatus': 0}, {'complementary_item': 'X', 'amount_usd': -5}),
            ('CN Complementary Item', {'docstatus': 2}, {'complementary_item': 'X', 'amount_usd': -5}),
            ('CN Complementary Item', {'origin_deposit': 'D'}, {'complementary_item': 'X', 'amount_usd': -5}),
            ('CN Complementary Item', {}, {'complementary_item': 'X', 'amount_usd': 5}),
            ('CN Complementary Item', {}, {'complementary_item': 'X', 'amount_usd': -11}),
            ('CN Source Row', {}, {'historical_application': 'MISSING', 'amount_usd': 5}),
            ('CN Source Row', {}, {'historical_application': 'APP', 'amount_usd': 0}),
            ('CN Source Row', {}, {'historical_application': 'APP', 'amount_usd': -5}),
            ('CN Source Row', {}, {'historical_application': 'APP', 'period': 'P', 'amount_usd': 5}),
            ('CN Source Row', {}, {'historical_application': 'APP', 'complementary_item': 'X', 'amount_usd': 5}),
            ('CN Complementary Item', {}, {'complementary_item': 'MISSING', 'amount_usd': -5}),
            ('CN Reconciliation Period', {}, {'period': 'P', 'row_key': 'MISSING', 'amount_usd': 5}),
        ]
        for category in ('Vinculado a depósito', 'Saldo a favor de la empresa', 'Saldo a favor del cliente',
                         controller.TOLERANCE_CATEGORY, 'Ajuste de aplicación', 'Compensación entre partidas'):
            cases.append(('CN Complementary Item', {'category': category}, {'complementary_item': 'X', 'amount_usd': -5}))
        original = copy.deepcopy(self.rows)
        for doctype, changes, target in cases:
            with self.subTest(doctype=doctype, changes=changes, target=target):
                self.rows = copy.deepcopy(original)
                self.rows[doctype][0].update(changes)
                errors = []
                for batched in (False, True):
                    with self.assertRaises(frappe.ValidationError) as caught:
                        self.validate_target(frappe._dict(target), batched)
                    errors.append(str(caught.exception))
                self.assertEqual(*errors)

    def test_collection_rows_are_loaded_once_even_with_many_destinations(self):
        targets = [frappe._dict(period='P', row_key='ROW', amount_usd=1) for _ in range(100)]
        context = self.context(targets)
        with patch.object(frappe, 'get_doc', side_effect=self.get_doc) as read:
            for target in targets:
                controller.CNRemittanceAllocation._validate_target(target, context=context)
        read.assert_called_once_with('CN Reconciliation Period', 'P')

    def test_new_validation_reads_changed_application_and_period_state(self):
        target = frappe._dict(historical_application='APP', amount_usd=5)
        self.validate_target(target, True)
        self.rows['CN Source Row'][0].effective = 0
        with self.assertRaises(frappe.ValidationError):
            self.validate_target(target, True)
        self.rows['CN Source Row'][0].effective = 1
        self.rows['CN Reconciliation Period'][0].status = 'Cerrado'
        with self.assertRaises(frappe.ValidationError):
            self.validate_target(target, True)

    def test_saved_links_check_closed_periods_in_batches_including_complements(self):
        entries = [dict(periodo='P')] * 500 + [dict(partida='X')] * 500
        with patch.object(frappe, 'get_all', side_effect=self.get_all):
            self.assertFalse(validation.allocation_has_closed_period(entries))
            self.assertEqual(len(self.queries), 2)
            self.rows['CN Reconciliation Period'][0].status = 'Cerrado'
            self.assertTrue(validation.allocation_has_closed_period(entries))
            self.assertTrue(validation.allocation_has_closed_period([dict(partida='X')]))
        self.assertEqual(entries, [dict(periodo='P')] * 500 + [dict(partida='X')] * 500)

    def test_related_period_guard_reads_saved_allocations_not_unsaved_form(self):
        self.rows['CN Reconciliation Period'][0].status = 'Cerrado'
        doc = frappe._dict(doctype='CN Remittance Allocation', name='D', allocation_detail='[]')
        with patch.object(frappe.db, 'get_value', return_value=json.dumps([dict(partida='X')])), \
                patch.object(frappe, 'get_all', side_effect=self.get_all), \
                patch.object(frappe, 'throw', side_effect=self.reject), \
                self.assertRaises(frappe.ValidationError):
            controller.CNRemittanceAllocation._assert_open_related_periods(doc)

    def test_generic_item_keeps_read_permission_and_reuses_only_within_validation(self):
        target = frappe._dict(complementary_item='X', amount_usd=-5)
        context = self.context([target])
        item = frappe._dict(check_permission=Mock())
        with patch.object(frappe, 'get_doc', return_value=item) as read:
            self.assertIs(context.generic_item('X'), item)
            self.assertIs(context.generic_item('X'), item)
            read.assert_called_once()
            item.check_permission.assert_called_once_with('read')
        with patch.object(frappe, 'get_doc', return_value=frappe._dict(
                check_permission=Mock(side_effect=frappe.PermissionError))), self.assertRaises(frappe.PermissionError):
            self.context([target]).generic_item('X')

    def test_full_validation_still_rejects_destinations_of_another_company(self):
        self.rows['CN Reconciliation Period'][0].employer = 'OTHER'
        doc = frappe._dict(doctype='CN Remittance Allocation', name='D', docstatus=1, employer='E',
            deposit_date='2025-05-01', deposit_currency='USD', deposit_amount=100, deposit_reference='D',
            detail_rows=[], detail_periods=[], targets=[frappe._dict(historical_application='APP', amount_usd=5)],
            _assert_open_related_periods=Mock(), _validate_target=controller.CNRemittanceAllocation._validate_target)
        def get_all(doctype, **kwargs):
            return [] if doctype == doc.doctype else self.get_all(doctype, **kwargs)
        with patch.object(frappe, 'get_all', side_effect=get_all), \
                patch.object(controller, 'allowed_employers', return_value={'E'}), \
                patch.object(frappe, 'throw', side_effect=self.reject), \
                self.assertRaisesRegex(frappe.ValidationError, 'empresa no autorizada'):
            controller.CNRemittanceAllocation._validate_deposit(doc)

    def test_endpoint_validates_fresh_document_once_after_cash_lock(self):
        events = []
        doc = frappe._dict(doctype='CN Remittance Allocation', name='D', employer='E',
            docstatus=1, apply_fifo=0, detail_rows=[], check_permission=Mock())
        doc.reload = Mock(side_effect=lambda: events.append('reload'))
        def validate():
            events.append('validate')
            raise frappe.ValidationError('Invalid refreshed destination')
        doc._validate_deposit = Mock(side_effect=validate)
        with patch.object(frappe, 'get_doc', return_value=doc), \
                patch.object(frappe, 'has_permission', return_value=True), \
                patch('credinomina_reconciliation.paying_employers.reconciliation_companies', return_value=['E']), \
                patch.object(scoped, 'lock_cash_pool', side_effect=lambda _companies: events.append('lock')), \
                patch('credinomina_reconciliation.manual_complementary_policy.assert_no_legacy_tolerance'), \
                patch('credinomina_reconciliation.remittance_detail_recovery.recover_reimported_detail'), \
                self.assertRaisesRegex(frappe.ValidationError, 'Invalid refreshed destination'):
            controller.reconcile_remittance('D')
        self.assertEqual(events, ['lock', 'reload', 'validate'])
        doc._validate_deposit.assert_called_once_with()

    def test_fifo_period_requirement_uses_state_reloaded_under_lock(self):
        doc = frappe._dict(doctype='CN Remittance Allocation', name='D', employer='E',
            docstatus=1, apply_fifo=0, detail_rows=[], check_permission=Mock(), _validate_deposit=Mock())
        doc.reload = Mock(side_effect=lambda: doc.update(apply_fifo=1, detail_rows=[frappe._dict(name='R')]))
        with patch.object(frappe, 'get_doc', return_value=doc), \
                patch.object(frappe, 'has_permission', return_value=True), \
                patch.object(frappe, 'throw', side_effect=self.reject), \
                patch('credinomina_reconciliation.paying_employers.reconciliation_companies', return_value=['E']), \
                patch.object(scoped, 'lock_cash_pool'), \
                patch('credinomina_reconciliation.manual_complementary_policy.assert_no_legacy_tolerance'), \
                self.assertRaisesRegex(frappe.ValidationError, 'Seleccione al menos un período'):
            controller.reconcile_remittance('D')
        doc._validate_deposit.assert_not_called()
