import json
import unittest
from collections import defaultdict
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation.application_link_preservation import preserve
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


class Row(frappe._dict):
    def as_dict(self):
        return dict(self)


class ApplicationLinkPreservationTests(unittest.TestCase):
    def source(self, **values):
        return Row(dict(name='OLD', event_type='Aplicacion', effective=1, match_status='Conciliado',
            currency='USD', amount=80, event_date='2026-09-15', loan_number='109-1', client_number='1',
            historical_period='P', collection_period='P', collection_row_id='C80',
            application_allocation_detail='[{"collection_row_id":"C80","period":"P","amount_usd":80}]',
            _source_employer='E', _source_import='I'), **values)

    def test_existing_quota_remains_reserved_when_new_application_matches_first(self):
        old = self.source()
        original = old.as_dict()
        old.update(match_status='Pendiente', application_allocation_detail='[]', collection_row_id='')
        new = self.source(name='NEW', match_status='Pendiente', collection_row_id='', application_allocation_detail='[]')
        targets = [Row(name=f'C{amount}', parent='P', expected_usd=amount, client_number='1', loan_number='109-1') for amount in (100, 80)]
        fixed = preserve([new, old], [original], targets)
        resolver = Mock()
        resolver.resolve.return_value = ('E', '')
        period = Row(name='P', employer='E', reconciliation_mode='Operativa')
        with patch.object(engine, 'load_client_index', return_value=[]), \
             patch.object(frappe, 'get_all', return_value=[]), \
             patch.object(engine, 'attach_employer_aliases'), \
             patch.object(engine, 'AccountingEmployerResolver', return_value=resolver):
            engine._match_applications([new], targets, [], defaultdict(list), [period], fixed_sources=fixed)
        self.assertEqual(old.application_allocation_detail, original['application_allocation_detail'])
        self.assertEqual(old.collection_row_id, 'C80')
        self.assertEqual(new.collection_row_id, 'C100')

    def test_single_target_adjustment_changes_only_amount(self):
        source = self.source(application_adjustment_usd=20)
        old = dict(source, application_adjustment_usd=0)
        fixed = preserve([source], [old], [Row(name='C80', parent='P')])
        self.assertEqual(fixed, [source])
        self.assertEqual(json.loads(source.application_allocation_detail),
                         [dict(collection_row_id='C80', period='P', amount_usd=60)])

    def test_partial_link_keeps_unlinked_remainder_on_an_unchanged_refresh(self):
        raw = '[{ "collection_row_id": "C80", "period": "P", "amount_usd": 40 }]'
        source = self.source(amount=100, application_allocation_detail=raw)
        original = source.as_dict()
        source.application_allocation_detail = '[]'
        fixed = preserve([source], [original], [Row(name='C80', parent='P')])
        self.assertEqual(fixed, [source])
        self.assertEqual(source.application_allocation_detail, raw)
        self.assertEqual(json.loads(source.application_allocation_detail)[0]['amount_usd'], 40)

    def test_adjusting_partial_link_does_not_invent_which_portion_is_reduced(self):
        source = self.source(amount=100,
            application_allocation_detail='[{"collection_row_id":"C80","period":"P","amount_usd":40}]')
        original = source.as_dict()
        source.application_adjustment_usd = 20
        with patch.object(frappe, 'throw', side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
            preserve([source], [original], [Row(name='C80', parent='P')])

    def test_existing_overlinked_evidence_is_not_silently_normalized(self):
        source = self.source(amount=30,
            application_allocation_detail='[{"collection_row_id":"C80","period":"P","amount_usd":40}]')
        with patch.object(frappe, 'throw', side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
            preserve([source], [source.as_dict()], [Row(name='C80', parent='P')])

    def test_missing_destination_or_ambiguous_split_change_blocks_instead_of_rematching(self):
        source = self.source()
        with patch.object(frappe, 'throw', side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
            preserve([source], [source.as_dict()], [])
        source.application_allocation_detail = '[{"collection_row_id":"C80","amount_usd":40},{"collection_row_id":"C2","amount_usd":40}]'
        old = source.as_dict()
        source.application_adjustment_usd = 10
        with patch.object(frappe, 'throw', side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
            preserve([source], [old], [Row(name='C80', parent='P'), Row(name='C2', parent='P')])

    def test_explicit_unassignment_is_not_restored(self):
        source = self.source()
        self.assertEqual(preserve([source], [source.as_dict()], [], exclude_import='I'), [])

    def test_existing_child_id_cannot_move_the_confirmed_application_to_another_credit_or_client(self):
        for values in (dict(loan_number='999-1'), dict(client='2'), dict(installment_number='3')):
            source = self.source(installment_number='2')
            target = Row(name='C80', parent='P', loan_number='109-1', client='1', installment_number='2')
            target.update(values)
            with self.subTest(values=values), patch.object(frappe, 'throw', side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
                preserve([source], [source.as_dict()], [target])

    def test_incompatible_currency_or_identity_does_not_bypass_matching_rules(self):
        for changes in (dict(currency='NIO'), dict(loan_number='OTHER-1')):
            source = self.source()
            original = source.as_dict()
            source.update(changes)
            with self.subTest(changes=changes), patch.object(frappe, 'throw', side_effect=frappe.ValidationError), self.assertRaises(frappe.ValidationError):
                preserve([source], [original], [Row(name='C80', parent='P')])

    def test_global_reconciliation_cannot_replay_deposits_even_with_legacy_false_argument(self):
        stored = dict(allocations=[], rounding_movements=[], deposit_meta={}, registered_ids={})
        allocation = dict(complementary_target={}, complementary_totals={})
        with patch.object(frappe, 'has_permission', return_value=True), \
             patch.object(frappe, 'get_all', return_value=[]), \
             patch('credinomina_reconciliation.paying_employers.reconciliation_companies', return_value=['E']), \
             patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool'), \
             patch('credinomina_reconciliation.deposit_reconciliation.stored_cash_context', return_value=stored), \
             patch.object(engine, 'load_scoped_imports', return_value=[]), \
             patch.object(engine, '_load_open_periods', return_value=[]), \
             patch.object(engine, '_allocate_complementary_items', return_value={}), \
             patch.object(engine, '_registered_deposit_pairs', return_value=([], {})), \
             patch.object(engine, '_operative_links', return_value={}), \
             patch.object(engine, '_distribute_deposits', return_value=allocation) as distribute, \
             patch.object(engine, '_rebuild_period_balances'), \
             patch.object(engine, '_rebuild_historical_balances'), \
             patch.object(engine, '_sync_registered_deposit_detail') as sync:
            result = engine._reconcile_sources('E', preserve_deposits=False)
        self.assertEqual(distribute.call_args.args[2], [])
        self.assertEqual(distribute.call_args.args[5:7], ([], {}))
        sync.assert_not_called()
        self.assertEqual(result['preserved_deposits'], 0)
