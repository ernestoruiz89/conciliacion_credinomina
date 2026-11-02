import copy
import json
import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.collection_link_preservation import guard


class Period(frappe._dict):
    def get_doc_before_save(self):
        return self.previous


class CollectionLinkPreservationTests(unittest.TestCase):
    def period(self, **changes):
        old = frappe._dict(name='C', idx=1, row_key='K', client='1', loan_number='109-1',
            employee_number='E1', national_id='ID1', installment_number='2', applied_usd=0,
            remitted_usd=0, client_name='Nombre original')
        current = frappe._dict(dict(old, **changes))
        return Period(name='P', employer='E', collection_rows=[current],
            previous=frappe._dict(name='P', employer='E', collection_rows=[old]))

    def check(self, period, evidence=None, error=False):
        evidence = evidence or {}
        before = copy.deepcopy(dict(period))
        with patch('credinomina_reconciliation.deposit_reconciliation.lock_cash_pool') as lock, \
             patch('credinomina_reconciliation.paying_employers.reconciliation_companies', return_value=['E']), \
             patch.object(frappe, 'get_all', side_effect=lambda doctype, **kwargs: evidence.get(doctype, [])) as read, \
             patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
            if error:
                with self.assertRaises(frappe.ValidationError):
                    guard(period)
            else:
                guard(period)
        self.assertEqual(dict(period), before)
        return lock, read

    def test_existing_application_cannot_be_moved_to_another_client_by_keeping_row_id(self):
        period = self.period(client='2')
        evidence = {'CN Source Row': [frappe._dict(collection_row_id='C', application_allocation_detail='[]')]}
        lock, _read = self.check(period, evidence, error=True)
        lock.assert_called_once_with({'E'})

    def test_source_json_link_protects_zero_or_stale_cached_amounts(self):
        evidence = {'CN Source Row': [frappe._dict(collection_row_id='',
            application_allocation_detail='[{"collection_row_id":"C","amount_usd":100}]')]}
        self.check(self.period(loan_number='999-1'), evidence, error=True)

    def test_application_amount_protects_identity_without_looking_for_more_evidence(self):
        period = self.period(installment_number='3')
        period.previous.collection_rows[0].applied_usd = 100
        _lock, read = self.check(period, error=True)
        read.assert_not_called()

    def test_submitted_target_reserves_original_destination_identity(self):
        self.check(self.period(row_key='OTHER'),
            {'CN Remittance Target': [frappe._dict(row_key='K')]}, error=True)

    def test_legacy_executed_cash_is_protected_without_a_live_source_or_planned_target(self):
        self.check(self.period(client='2'), {'CN Remittance Allocation': [frappe._dict(
            allocation_detail=json.dumps([dict(tipo='Cobranza', periodo='P', fila_id='K', importe_usd=100)]))]}, error=True)

    def test_removal_of_a_linked_quota_is_blocked_even_when_cache_is_zero(self):
        period = self.period()
        period.collection_rows = []
        self.check(period, {'CN Source Row': [frappe._dict(collection_row_id='C')]}, error=True)

    def test_names_comments_and_equivalent_credit_format_do_not_change_identity(self):
        period = self.period(client_name='Nombre corregido', loan_number='000109', client='001', comments='Comentario')
        _lock, read = self.check(period)
        read.assert_not_called()

    def test_missing_identity_can_be_completed_without_erasing_links(self):
        period = self.period(national_id='ID1')
        period.previous.collection_rows[0].national_id = ''
        _lock, read = self.check(period)
        read.assert_not_called()

    def test_unlinked_destination_can_be_corrected_and_other_period_cash_is_ignored(self):
        self.check(self.period(client='2'), {'CN Remittance Allocation': [frappe._dict(
            allocation_detail=json.dumps([dict(tipo='Cobranza', periodo='OTHER', fila_id='K', importe_usd=100)]))]})

    def test_appending_a_different_child_with_the_paid_row_key_is_rejected(self):
        period = self.period()
        period.collection_rows.append(frappe._dict(name='NEW', row_key=' K ', client='2', loan_number='999-1'))
        _lock, read = self.check(period, error=True)
        read.assert_not_called()

    def test_single_blank_legacy_key_is_not_reassigned(self):
        period = self.period(row_key='')
        period.previous.collection_rows[0].row_key = ''
        self.check(period)
        self.assertEqual(period.collection_rows[0].row_key, '')

    def test_executed_cash_with_a_blank_legacy_key_still_protects_its_customer(self):
        period = self.period(client='2', row_key='')
        period.previous.collection_rows[0].row_key = None
        self.check(period, {'CN Remittance Allocation': [frappe._dict(allocation_detail=json.dumps([
            dict(tipo='Cobranza', periodo='P', fila_id=None, importe_usd=100)]))]}, error=True)
