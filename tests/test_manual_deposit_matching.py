import datetime
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import manual_deposit_matching as matching
from credinomina_reconciliation.deposit_accounting_sources import grouped_source_fits


def planned(**changes):
    return dict(resolved_employer='EMP', bank_deposit_reference='REF', deposit_date='2026-10-05',
                deposit_currency='USD', deposit_amount=100, bank_deposit_currency='USD',
                bank_deposit_amount=100, voucher='ASIENTO', source_currency='USD', source_credit=100,
                **changes)


def manual(**changes):
    return frappe._dict({'name': 'MANUAL', 'docstatus': 1, 'employer': 'EMP', 'bank_account': 'BAC',
        'deposit_reference': 'REF', 'deposit_date': datetime.date(2026, 10, 5),
        'deposit_currency': 'USD', 'deposit_amount': 100, 'amount_usd': 100, 'deposit_voucher': '', **changes})


class ManualDepositMatchingTests(unittest.TestCase):
    def test_exact_draft_or_confirmed_receipt_without_voucher_matches(self):
        for status in (0, 1):
            for voucher in ('', 'ASIENTO'):
                with self.subTest(status=status, voucher=voucher):
                    self.assertTrue(matching.exact_manual_match(manual(docstatus=status, deposit_voucher=voucher), planned(), 'BAC'))

    def test_partial_conflicting_imported_or_canceled_receipts_require_review(self):
        for changes in ({'deposit_voucher': 'OTRO'}, {'bank_account': ''}, {'bank_account': 'NO IDENTIFICADA'},
                        {'deposit_reference': 'OTRA'}, {'deposit_date': '2026-10-06'}, {'deposit_amount': 101},
                        {'deposit_currency': 'NIO'}, {'employer': 'OTRA'}, {'docstatus': 2},
                        {'accounting_source_key': 'old'}, {'accounting_sources': [{'source_row': 1}]},
                        {'consolidated_deposit': 'OLD'}, {'cash_identity_status': 'Revisar identidad'}):
            with self.subTest(changes=changes):
                self.assertFalse(matching.exact_manual_match(manual(**changes), planned(), 'BAC'))
        for changes in ({'resolved_employer': 'NO IDENTIFICADA'}, {'bank_deposit_currency': ''}, {'bank_deposit_amount': 0}):
            with self.subTest(changes=changes):
                self.assertFalse(matching.exact_manual_match(manual(), {**planned(), **changes}, 'BAC'))
        self.assertFalse(matching.exact_manual_match(manual(), planned(), 'NO IDENTIFICADA'))

    def test_candidate_search_keeps_voucher_conflicts_and_unknown_bank(self):
        rows = [manual(deposit_voucher='OTRO'), manual(name='UNKNOWN', bank_account='NO IDENTIFICADA'),
                manual(name='OTHER_BANK', bank_account='LAFISE'), manual(name='LINKED', cash_identity_status='Origen vinculado')]
        with patch.object(frappe, 'get_all', return_value=rows) as query:
            self.assertEqual([item.name for item in matching.find_candidates(planned(), 'BAC')], ['MANUAL', 'UNKNOWN'])
            self.assertNotIn('deposit_voucher', query.call_args.kwargs['filters'])
            self.assertEqual(query.call_args.kwargs['limit_page_length'], 0)

    def test_changed_reference_is_review_candidate_by_date_and_amount(self):
        candidate = manual(deposit_reference='TYPO')
        with patch.object(frappe, 'get_all', side_effect=[[], [candidate]]) as query:
            self.assertEqual(matching.find_candidates(planned(), 'BAC'), [candidate])
            self.assertEqual(query.call_args.kwargs['filters']['employer'], 'EMP')
        self.assertFalse(matching.exact_manual_match(candidate, planned(), 'BAC'))

    def test_unknown_company_only_searches_reference_and_never_auto_links(self):
        row = {**planned(), 'resolved_employer': 'NO IDENTIFICADA'}
        with patch.object(frappe, 'get_all', return_value=[manual()]) as query:
            self.assertEqual(len(matching.find_candidates(row, 'BAC')), 1)
            self.assertNotIn('employer', query.call_args.kwargs['filters'])
            query.assert_called_once()
        self.assertFalse(matching.exact_manual_match(manual(), row, 'BAC'))

    def test_multiple_candidates_are_never_auto_linked(self):
        with patch.object(frappe, 'get_doc') as get:
            self.assertIsNone(matching.link_manual_candidate([manual(), manual(name='SECOND')], planned(), 'BAC', 'file', 'hash', 'group'))
            get.assert_not_called()

    def test_locked_receipt_is_rechecked_and_write_permission_is_required(self):
        for changed, permission in ((True, True), (False, False), (False, True)):
            doc = manual(deposit_amount=101 if changed else 100, has_permission=Mock(return_value=permission))
            with patch.object(frappe, 'get_doc', return_value=doc) as get, \
                    patch('credinomina_reconciliation.deposit_accounting_sources.record_imported_source') as record:
                result = matching.link_manual_candidate([manual()], planned(), 'BAC', 'file', 'hash', 'group')
                get.assert_called_once_with(matching.DOCTYPE, 'MANUAL', for_update=True)
                if changed or not permission:
                    self.assertIsNone(result)
                    record.assert_not_called()
                else:
                    self.assertIs(result, doc)
                    record.assert_called_once_with(doc, planned(), 'file', 'hash', 'group')

    def test_extra_turnover_cannot_change_confirmed_cash(self):
        doc = manual(amount_usd=100, accounting_sources=[frappe._dict(source_currency='USD', source_credit=90)])
        self.assertTrue(grouped_source_fits(doc, {**planned(), 'source_credit': 10}))
        self.assertFalse(grouped_source_fits(doc, {**planned(), 'source_credit': 100}))
