import unittest
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_reimport_state import reconciliation_state
from credinomina_reconciliation.accounting_cash_guard import guard_cash_changes


class AccountingReimportStateTests(unittest.TestCase):
    def source(self):
        return dict(name='APP', event_type='Aplicacion', source_key='KEY', accounting_source_key='ORIGIN',
            currency='USD', amount=100, effective=1, event_date='2026-09-30', client_number='1', loan_number='L',
            historical_period='P', processing_route='Operativa', manual_fx_rate=0, match_status='Conciliado',
            collection_period='P', collection_row_id='C',
            application_allocation_detail='[{"collection_row_id":"C","period":"P","amount_usd":40}]',
            deposit_match_status='Depósito parcial', historical_remitted_usd=40, historical_balance_usd=60,
            historical_detail='[{"referencia":"D","importe_usd":40}]', quality_status='Con diferencias')

    def test_identical_reimport_keeps_exact_payroll_split_and_cash_evidence(self):
        previous = self.source()
        incoming = {field: value for field, value in previous.items() if field not in {
            'match_status', 'collection_period', 'collection_row_id', 'application_allocation_detail',
            'deposit_match_status', 'historical_remitted_usd', 'historical_balance_usd', 'historical_detail', 'quality_status'}}
        incoming.update(match_status='Pendiente', deposit_match_status='Pendiente')
        incoming.update(reconciliation_state(previous, incoming))
        for field in ('name', 'collection_row_id', 'application_allocation_detail', 'historical_remitted_usd',
                      'historical_balance_usd', 'historical_detail', 'match_status', 'quality_status'):
            self.assertEqual(incoming[field], previous[field])
        old_doc = frappe._dict(name='IMPORT', employer='E', source_file='/private/source.csv',
                              file_hash='HASH', status='Importado', rows=[frappe._dict(previous)])
        new_doc = frappe._dict(old_doc, rows=[frappe._dict(incoming)], get_doc_before_save=lambda: old_doc)
        with patch('credinomina_reconciliation.accounting_cash_guard.cash_coverage') as coverage:
            guard_cash_changes(new_doc, unpaid_refresh=True)
        coverage.assert_not_called()

    def test_period_identity_and_amount_changes_never_copy_old_links(self):
        previous = self.source()
        for changes in (dict(historical_period='OTHER'), dict(historical_period=''), dict(amount=90),
                        dict(currency='NIO'), dict(loan_number='OTHER'), dict(event_date='2026-09-15'),
                        dict(effective=0), dict(event_type='Ajuste')):
            with self.subTest(changes=changes):
                self.assertEqual(reconciliation_state(previous, dict(previous, **changes)), {})

    def test_new_rows_cannot_acquire_another_sources_reconciliation(self):
        self.assertEqual(reconciliation_state({}, self.source()), {})
        self.assertEqual(reconciliation_state(self.source(), dict(self.source(), source_key='NEW')), {})
