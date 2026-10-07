import unittest
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
