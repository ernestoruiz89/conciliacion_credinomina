import unittest
import json
from decimal import Decimal
from unittest.mock import patch
import frappe
from credinomina_reconciliation import accounting_cash_guard as guard


class AccountingCashGuardTests(unittest.TestCase):
    def setUp(self):
        self.payer = patch.object(guard, 'reconciliation_companies', side_effect=lambda company: [company])
        self.lock = patch.object(guard, 'lock_cash_pool')
        self.payer.start()
        self.lock_mock = self.lock.start()
        self.addCleanup(self.payer.stop)
        self.addCleanup(self.lock.stop)
    def document(self, rows=None, **changes):
        old = frappe._dict(employer='A', status='Importado', rows=[frappe._dict(name='APP', idx=1,
            event_type='Aplicacion', amount=100, currency='USD', source_key='origin',
            effective=1, match_status='Conciliado')])
        doc = frappe._dict(old, rows=old.rows if rows is None else rows, **changes)
        doc.get_doc_before_save = lambda: old
        return doc

    def test_deletion_and_identity_change_are_protected_without_closed_period(self):
        edited = self.document().rows[0].copy()
        edited['amount'] = 90
        for doc, deleting in [(self.document(), True), (self.document(rows=[]), False),
                              (self.document(rows=[frappe._dict(edited)]), False),
                              (self.document(employer='B'), False)]:
            for coverage in [dict(protected_usd=100, snapshots={}),
                             dict(protected_usd=0, snapshots={'DEP': 'cash'})]:
                with self.subTest(deleting=deleting, coverage=coverage), \
                     patch.object(guard, 'cash_coverage', return_value=coverage), \
                     patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
                    with self.assertRaises(frappe.ValidationError):
                        guard.guard_cash_changes(doc, deleting=deleting)

    def test_unchanged_row_does_not_scan_deposits(self):
        with patch.object(guard, 'cash_coverage') as read:
            guard.guard_cash_changes(self.document(notes='Observación'))
        read.assert_not_called()
        self.lock_mock.assert_not_called()

    def test_save_accepts_equivalent_currency_serialization(self):
        for field in ('historical_remitted_usd', 'historical_balance_usd'):
            for stored, submitted in [(100.0, 100), (Decimal('100.00'), 100),
                                      (100.50, '100.500'), (None, 0), (0.0, '0')]:
                with self.subTest(field=field, stored=stored, submitted=submitted):
                    doc = self.document()
                    old = frappe._dict(doc.rows[0], **{field: stored})
                    doc.get_doc_before_save = lambda: frappe._dict(employer='A', status='Importado', rows=[old])
                    doc.rows = [frappe._dict(old, **{field: submitted})]
                    with patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=0, snapshots={})) as read, \
                         patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
                        guard.guard_cash_changes(doc)
                    read.assert_not_called()
        self.lock_mock.assert_not_called()

    def test_unpaid_row_still_rejects_real_currency_changes(self):
        for field in ('historical_remitted_usd', 'historical_balance_usd'):
            doc = self.document()
            old = frappe._dict(doc.rows[0], **{field: 100.0})
            doc.get_doc_before_save = lambda: frappe._dict(employer='A', status='Importado', rows=[old])
            doc.rows = [frappe._dict(old, **{field: 100.01})]
            with self.subTest(field=field), \
                 patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=0, snapshots={})), \
                 patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
                 self.assertRaises(frappe.ValidationError):
                guard.guard_cash_changes(doc)

    def test_unpaid_application_can_be_removed(self):
        with patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=0, snapshots={})):
            guard.guard_cash_changes(self.document(rows=[]))
        self.lock_mock.assert_called_once_with({'A'})

    def test_company_change_locks_both_scopes_before_reading_cash(self):
        order = []
        self.lock_mock.side_effect = lambda companies: order.append(('lock', companies))
        with patch.object(guard, 'cash_coverage', side_effect=lambda row: (order.append(('cash', row.name)) or dict(protected_usd=0, snapshots={}))):
            guard.guard_cash_changes(self.document(employer='B'))
        self.assertEqual(order, [('lock', {'A', 'B'}), ('cash', 'APP')])

    def test_paid_or_reserved_application_cannot_be_hidden(self):
        for field, value in [('effective', 0), ('effective', 2),
                             ('match_status', 'Ignorado'), ('remittance_allocation', 'DEP'),
                             ('complementary_item', 'COMP')]:
            row = frappe._dict(self.document().rows[0], **{field: value})
            for coverage in [dict(protected_usd=90, snapshots={'DEP': 'cash'}),
                             dict(protected_usd=100, snapshots={})]:
                with self.subTest(field=field, coverage=coverage), \
                     patch.object(guard, 'cash_coverage', return_value=coverage), \
                     patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
                    with self.assertRaises(frappe.ValidationError):
                        guard.guard_cash_changes(self.document(rows=[row]))

    def test_paid_import_cannot_leave_the_reported_states(self):
        for status in ['Borrador', '', 'Otro estado']:
            with self.subTest(status=status), \
                 patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=90, snapshots={})), \
                 patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
                with self.assertRaises(frappe.ValidationError):
                    guard.guard_cash_changes(self.document(status=status))

    def test_normal_import_status_transition_and_match_refresh_are_allowed(self):
        row = frappe._dict(self.document().rows[0], match_status='Enlace provisional')
        with patch.object(guard, 'cash_coverage') as read:
            guard.guard_cash_changes(self.document(status='Importado con excepciones'))
            guard.guard_cash_changes(self.document(rows=[row]), verified_reconciliation=True)
        read.assert_not_called()
        self.lock_mock.assert_not_called()

    def test_unpaid_eligibility_changes_use_lock_but_are_not_blocked(self):
        row = frappe._dict(self.document().rows[0], effective=0)
        with patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=0, snapshots={})) as read:
            guard.guard_cash_changes(self.document(rows=[row], status='Borrador'))
        read.assert_called_once()
        self.lock_mock.assert_called_once_with({'A'})

    def test_financial_results_and_routing_cannot_be_rewritten_outside_verified_reconciliation(self):
        for field, value in [('historical_remitted_usd', 0), ('historical_balance_usd', 100),
                             ('historical_detail', '[]'), ('match_status', 'Sin coincidencia'),
                             ('application_allocation_detail', '[]'), ('collection_row_id', 'OTHER'),
                             ('collection_period', 'OTHER'), ('historical_period', 'OTHER'),
                             ('processing_route', 'Operativa')]:
            doc = self.document()
            old = frappe._dict(doc.rows[0], historical_remitted_usd=100, historical_balance_usd=0,
                               historical_detail='[{"deposito":"DEP"}]',
                               application_allocation_detail='[{"collection_row_id":"ROW"}]',
                               collection_row_id='ROW', collection_period='P', historical_period='H',
                               processing_route='Historica')
            doc.rows = [frappe._dict(old, **{field: value})]
            doc.get_doc_before_save = lambda: frappe._dict(employer='A', status='Importado', rows=[old])
            with self.subTest(field=field), patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=90, snapshots={})), \
                 patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
                with self.assertRaises(frappe.ValidationError):
                    guard.guard_cash_changes(doc)

    def test_verified_reconciliation_still_cannot_rewrite_original_amount_or_routing(self):
        for field, value in [('amount', 200), ('historical_period', 'OTHER'), ('effective', 0)]:
            doc = self.document(rows=[frappe._dict(self.document().rows[0], **{field: value})])
            with self.subTest(field=field), patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=90, snapshots={})), \
                 patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
                with self.assertRaises(frappe.ValidationError):
                    guard.guard_cash_changes(doc, verified_reconciliation=True)

    def test_unpaid_row_cannot_fabricate_cash_or_distribution_results(self):
        row = frappe._dict(self.document().rows[0], historical_remitted_usd=100)
        with patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=0, snapshots={})), \
             patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
             self.assertRaises(frappe.ValidationError):
            guard.guard_cash_changes(self.document(rows=[row]))

    def test_import_may_refresh_only_unpaid_calculated_results(self):
        row = frappe._dict(self.document().rows[0], match_status='Pendiente')
        doc = self.document(rows=[row])
        with patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=0, snapshots={} )):
            guard.guard_cash_changes(doc, unpaid_refresh=True)
        with patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=90, snapshots={})), \
             patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
             self.assertRaises(frappe.ValidationError):
            guard.guard_cash_changes(doc, unpaid_refresh=True)

    def test_new_application_cannot_arrive_with_invented_deposit_results(self):
        for field, value in [('historical_remitted_usd', 100),
                             ('historical_detail', '[{"deposit":"FAKE"}]'),
                             ('application_allocation_detail', '[{"amount_usd":100}]')]:
            doc = self.document(rows=[frappe._dict(name='NEW', event_type='Aplicacion', **{field: value})])
            for previous in [None, frappe._dict(rows=[])]:
                with self.subTest(field=field, previous=previous), \
                     patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
                     self.assertRaises(frappe.ValidationError):
                    doc.get_doc_before_save = lambda: previous
                    guard.guard_cash_changes(doc)

    def test_first_period_assignment_batches_only_after_lock_and_keeps_derived_guard(self):
        rows = [frappe._dict(name=f'APP-{i}', idx=i + 1, event_type='Aplicacion', amount=100,
                            currency='USD', effective=1) for i in range(3)]
        doc = self.document(rows=rows, historical_period='NEW')
        doc.get_doc_before_save = lambda: frappe._dict(employer='A', status='Importado', rows=rows)
        order = []
        self.lock_mock.side_effect = lambda companies: order.append('lock')
        def batch(changed):
            order.append('batch')
            self.assertEqual(changed, rows)
            return {'APP-0', 'APP-1'}
        with patch.object(guard, '_applications_without_cash', side_effect=batch), \
             patch.object(guard, 'cash_coverage', side_effect=lambda row: (
                 order.append(row.name) or dict(protected_usd=20, snapshots={}))), \
             patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
            with self.assertRaises(frappe.ValidationError):
                guard.guard_cash_changes(doc, unpaid_refresh=True)
        self.assertEqual(order, ['lock', 'batch', 'APP-2'])
        # Even an absence proof cannot authorize user-entered cash results.
        doc.rows = [frappe._dict(row, historical_remitted_usd=100) for row in rows]
        with patch.object(guard, '_applications_without_cash', return_value={row.name for row in rows}), \
             patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
            with self.assertRaises(frappe.ValidationError):
                guard.guard_cash_changes(doc)

    def test_filling_default_keeps_an_explicit_paid_historical_period(self):
        doc = self.document(historical_backfill=1, historical_period='APRIL')
        row = frappe._dict(doc.rows[0], historical_period='APRIL', historical_remitted_usd=100)
        doc.get_doc_before_save = lambda: frappe._dict(employer='A', status='Importado',
            historical_backfill=1, rows=[row])
        doc.rows = [row]
        with patch.object(guard, 'cash_coverage') as coverage:
            guard.guard_cash_changes(doc)
        coverage.assert_not_called()
        self.lock_mock.assert_not_called()

    def test_filling_default_keeps_a_paid_row_with_explicit_operative_route(self):
        doc = self.document(historical_period='SEP')
        row = frappe._dict(doc.rows[0], historical_period='SEP', processing_route='Operativa', historical_remitted_usd=100)
        doc.get_doc_before_save = lambda: frappe._dict(employer='A', status='Importado', rows=[row])
        doc.rows = [row]
        with patch.object(guard, 'cash_coverage') as coverage:
            guard.guard_cash_changes(doc)
        coverage.assert_not_called()

    def test_filling_default_cannot_move_paid_rows_or_change_origin(self):
        for explicit_period, new_period, changes in [('', 'APRIL', {}), ('APRIL', 'MAY', {}),
                                                    ('APRIL', 'APRIL', {'loan_number': 'NEW'})]:
            with self.subTest(explicit_period=explicit_period, new_period=new_period, changes=changes):
                doc = self.document(historical_backfill=1, historical_period=new_period)
                old = frappe._dict(doc.rows[0], historical_period=explicit_period, historical_remitted_usd=100)
                doc.get_doc_before_save = lambda: frappe._dict(employer='A', status='Importado',
                    historical_backfill=1, rows=[old])
                doc.rows = [frappe._dict(old, **changes)]
                with patch.object(guard, 'cash_coverage', return_value=dict(protected_usd=100, snapshots={})), \
                     patch.object(frappe, 'throw', side_effect=frappe.ValidationError), \
                     self.assertRaises(frappe.ValidationError):
                    guard.guard_cash_changes(doc)

    def test_batch_proves_absence_without_using_stored_pending_status(self):
        rows = [frappe._dict(name=f'APP-{i}', historical_remitted_usd=0,
                            deposit_match_status='Sin deposito') for i in range(542)]
        rows += [frappe._dict(name='PAYROLL', collection_row_id='C'),
                 frappe._dict(name='SPLIT', application_allocation_detail='[{"collection_row_id":"C"}]'),
                 frappe._dict(name='STORED', historical_remitted_usd=1)]
        calls = []
        def query(doctype, **kwargs):
            calls.append(doctype)
            self.assertNotIn('employer', kwargs['filters'])
            if doctype == 'CN Remittance Target':
                self.assertEqual(kwargs['filters']['docstatus'], ['<', 2])
                return ['APP-0'] if 'APP-0' in kwargs['filters']['historical_application'][1] else []
            self.assertEqual(kwargs['filters']['docstatus'], 1)
            return [frappe._dict(name='CASH', allocation_detail=json.dumps([
                {'tipo': 'Aplicacion historica', 'aplicacion_id': 'APP-1', 'importe_usd': 0},
                {'tipo': 'Aplicacion historica', 'aplicacion_id': 'APP-2', 'importe_usd': -1},
                {'tipo': 'Aplicacion historica', 'aplicacion_id': 'APP-30-OTHER', 'importe_usd': 10}]))]
        with patch.object(frappe, 'get_all', side_effect=query), \
             patch('credinomina_reconciliation.credit_application.funding_totals', return_value={'APP-3': 10, 'APP-4': 0}):
            actual = guard._applications_without_cash(rows)
        self.assertEqual(actual, {f'APP-{i}' for i in range(4, 542)})
        self.assertEqual(calls, ['CN Remittance Target', 'CN Remittance Target', 'CN Remittance Allocation'])

    def test_incomplete_journals_retain_individual_cash_validation(self):
        rows = [frappe._dict(name='A'), frappe._dict(name='B')]
        for raw in ('invalid', '{}', '[null]', '[{"tipo":"Aplicacion historica"}]'):
            with self.subTest(raw=raw), patch.object(frappe, 'get_all', side_effect=[[],
                    [frappe._dict(name='D', allocation_detail=raw)]]), \
                 patch('credinomina_reconciliation.credit_application.funding_totals') as funding:
                self.assertEqual(guard._applications_without_cash(rows), set())
                funding.assert_not_called()

    def test_batch_checks_all_pages_and_does_not_read_cash_when_all_rows_reserved(self):
        rows = [frappe._dict(name='A'), frappe._dict(name='B')]
        first = [frappe._dict(name=f'D-{i:03}', allocation_detail='[{"tipo":"Cobranza"}]') for i in range(500)]
        last = [frappe._dict(name='LAST', allocation_detail='[{"tipo":"Aplicacion historica","aplicacion_id":"B"}]')]
        with patch.object(frappe, 'get_all', side_effect=[[], first, last]) as query, \
             patch('credinomina_reconciliation.credit_application.funding_totals', return_value={}):
            self.assertEqual(guard._applications_without_cash(rows), {'A'})
        self.assertEqual(query.call_args.kwargs['filters']['name'], ['>', 'D-499'])
        with patch.object(frappe, 'get_all', return_value=['A', 'B']) as query:
            self.assertEqual(guard._applications_without_cash(rows), set())
        query.assert_called_once()
