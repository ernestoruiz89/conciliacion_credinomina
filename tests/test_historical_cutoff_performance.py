"""Bound the work without weakening historical accounting or permission checks."""
import copy
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import application_context, report_records
from credinomina_reconciliation.deposit_adjustment_receivables import build_receivables
from credinomina_reconciliation.historical_cutoff import reconstruct


class HistoricalCutoffPerformanceTests(unittest.TestCase):
    def test_current_journals_are_optional_and_cutoff_results_stay_identical(self):
        population = {
            'CN Reconciliation Period': [dict(name='P', employer='E', reconciliation_mode='Operativa')],
            'CN Accounting Import': [dict(name='I', employer='E')],
            'CN Employer': [dict(name='E', grace_days=10)],
            'CN Source Row': [dict(name='A', parent='I', event_type='Aplicacion', effective=1,
                match_status='Conciliado', event_date='2025-04-30', currency='USD', amount=100,
                collection_row_id='C', historical_detail='stale journal', historical_remitted_usd=999)],
            'CN Collection Row': [dict(name='C', parent='P', row_key='KEY', applied_usd=100,
                remittance_detail='stale journal', rounding_adjustment_usd=999, fx_variance_usd=999)],
        }
        calls = []

        def read(doctype, *args, fields, **kwargs):
            calls.append((doctype, fields))
            return [frappe._dict({field: row.get(field) for field in fields}) for row in population[doctype]]

        with patch.object(application_context, 'records', side_effect=read), \
                patch.object(application_context, 'child_records', side_effect=read):
            full = application_context.load_application_context()
            calls.clear()
            lean = application_context.load_application_context(include_current_settlements=False)
        fields = dict(calls)
        self.assertNotIn('historical_detail', fields['CN Source Row'])
        self.assertNotIn('remittance_detail', fields['CN Collection Row'])
        self.assertIn('application_allocation_detail', fields['CN Source Row'])
        self.assertIn('applied_usd', fields['CN Collection Row'])
        deposits = [dict(name='D', employer='OTHER', docstatus=1, deposit_date='2025-05-15',
            amount_usd=60, allocation_detail=[dict(tipo='Cobranza', periodo='P', fila_id='KEY', importe_usd=60)])]
        before = copy.deepcopy((full, lean, deposits))
        for day in ('2025-04-30', '2025-05-31'):
            self.assertEqual(reconstruct(full, deposits, [], [], day, include_bridge=True),
                             reconstruct(lean, deposits, [], [], day, include_bridge=True))
        self.assertEqual((full, lean, deposits), before)

    def test_permission_check_does_not_reload_visible_journals(self):
        names = [f'D{i:04}' for i in range(1101)]

        def query(doctype, **kwargs):
            self.assertEqual(kwargs['fields'], ['name'])
            start = kwargs['limit_start']
            return [frappe._dict(name=name) for name in names[start:start + kwargs['limit_page_length']]]

        relevant = Mock()
        with patch.object(frappe, 'get_all', side_effect=query) as read:
            report_records.require_complete_evidence('CN Remittance Allocation', names,
                filters={'docstatus': 1}, fields=['allocation_detail'], relevant=relevant)
        self.assertEqual(read.call_count, 3)
        relevant.assert_not_called()

    def test_hidden_journals_remain_bounded_and_fail_without_disclosure(self):
        names = [f'D{i:04}' for i in range(1001)]
        payload_batches = []

        def query(doctype, **kwargs):
            scope = kwargs['filters']
            self.assertEqual(scope['docstatus'], 1)
            self.assertEqual(scope['deposit_date'], ['<=', '2025-05-31'])
            wanted = scope.get('name', ['in', names])[1]
            if kwargs['fields'] != ['name']:
                payload_batches.append(wanted)
                self.assertLessEqual(len(wanted), report_records.PAGE_SIZE)
                self.assertNotIn('VISIBLE', wanted)
            start = kwargs['limit_start']
            return [frappe._dict(name=name, allocation_detail='linked' if name == names[-1] else '')
                    for name in wanted[start:start + kwargs['limit_page_length']]]

        def reject(message, exception):
            raise exception(message)

        with patch.object(frappe, 'get_all', side_effect=query), patch.object(frappe, 'throw', side_effect=reject):
            with self.assertRaises(frappe.PermissionError) as error:
                report_records.require_complete_evidence('CN Remittance Allocation', ['VISIBLE'],
                    filters={'docstatus': 1, 'deposit_date': ['<=', '2025-05-31']},
                    fields=['allocation_detail'], relevant=lambda row: bool(row.allocation_detail))
        self.assertEqual(len(payload_batches), 5)  # Full pages include an empty terminal query.
        self.assertNotIn(names[-1], str(error.exception))

    def test_recoveries_are_indexed_once_and_preserve_company_and_status(self):
        count = 1000
        items = [dict(name=f'I{i}', docstatus=1, category='Ajuste de conciliación',
            subcategory_effect='CxC a la empresa', amount_usd=-100, employer='E') for i in range(count)]
        # Use the actual configured effect, independent of its display label.
        from credinomina_reconciliation.complementary_subcategories import RECEIVABLE
        for item in items:
            item['subcategory_effect'] = RECEIVABLE
        balances = {item['name']: dict(distributions=[dict(employer=company, amount_usd=-100)
                    for company in ('E', 'OTHER')]) for item in items}
        receipts = [dict(name=f'R{i}', receivable_origin=item['name'], employer='E', docstatus=1,
                        category='Compensación entre partidas', compensated_usd=25) for i, item in enumerate(items)]
        receipts.append(dict(name='CANCELLED', receivable_origin='I0', employer='E', docstatus=2,
                             category='Compensación entre partidas', compensated_usd=100))

        class CountedRows(list):
            iterations = 0

            def __iter__(self):
                self.iterations += 1
                return super().__iter__()

        settlements = CountedRows(receipts)
        rows = build_receivables(items, balances, settlements=settlements)
        self.assertEqual(settlements.iterations, 1)
        self.assertEqual(len(rows), 2 * count)
        for row in rows:
            self.assertEqual(row['receivable_usd'], 75 if row['employer'] == 'E' else 100)
            self.assertEqual(len(row['recoveries']), 1 if row['employer'] == 'E' else 0)
