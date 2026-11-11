"""Bound the work without weakening historical accounting or permission checks."""
import copy
import unittest
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import application_context, historical_cutoff, report_records
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


class CutoffReportReuseTests(unittest.TestCase):
    def setUp(self):
        self.sources = [dict(name=name, parent='I' + name, event_type='Aplicacion', effective=1,
            currency='USD', amount=100, event_date='2026-03-15', historical_period='P' + name,
            match_status='Conciliado', client_number=name) for name in ('A', 'B')]
        self.context = (self.sources,
            {'I' + name: dict(name='I' + name, employer=name) for name in ('A', 'B')},
            {'P' + name: dict(name='P' + name, employer=name, payroll_month='2026-03-01',
                             reconciliation_mode='Historica') for name in ('A', 'B')}, {}, {})
        self.deposit = dict(name='D', employer='A', docstatus=1, deposit_date='2026-03-20', amount_usd=80,
            allocation_detail=[dict(tipo='Aplicacion historica', aplicacion_id=name, periodo='P' + name,
                importe_usd=amount) for name, amount in (('A', 60), ('B', 20))])
        self.filters = [dict(cutoff_date='2026-08-31', employer=name) for name in ('A', 'B')]
        self.reads = self.start_patch(historical_cutoff, 'records', side_effect=self.read)
        self.start_patch(historical_cutoff, 'child_records', return_value=[])
        self.context_read = self.start_patch(historical_cutoff, 'load_application_context', return_value=self.context)
        self.allowed = self.start_patch(frappe, 'has_permission', return_value=True)
        self.start_patch(frappe, 'session', frappe._dict(user='READER'))
        self.start_patch(frappe, 'throw', side_effect=lambda message, error=ValueError: (_ for _ in ()).throw(error(message)))
        check = patch('credinomina_reconciliation.cutoff_evidence.require_cutoff_evidence')
        self.evidence = check.start()
        self.addCleanup(check.stop)

    def start_patch(self, obj, field, *args, **kwargs):
        change = patch.object(obj, field, *args, **kwargs)
        self.addCleanup(change.stop)
        return change.start()

    def read(self, doctype, *, fields, **kwargs):
        return [frappe._dict({field: self.deposit.get(field) for field in fields})] if doctype == 'CN Remittance Allocation' else []

    @historical_cutoff.reuse_cutoff_projection
    def run_report(self, filters=None):
        return [historical_cutoff.load_cutoff(scope) for scope in (filters or self.filters)]

    def test_shared_payer_scopes_reuse_one_projection_and_preserve_exact_results(self):
        expected = [historical_cutoff.load_cutoff(scope) for scope in self.filters]
        self.context_read.reset_mock()
        self.evidence.reset_mock()
        before = copy.deepcopy((self.context, self.deposit))
        with patch.object(historical_cutoff, 'reconstruct', wraps=reconstruct) as project:
            actual = self.run_report()
        self.assertEqual(actual, expected)
        self.assertEqual([value['summary'][0]['application_pending_usd'] for value in actual], [40, 80])
        self.assertEqual(project.call_count, 1)
        self.context_read.assert_called_once()
        self.evidence.assert_called_once()
        self.assertEqual((self.context, self.deposit), before)
        self.assertIsNone(historical_cutoff._report_projections.get())

    def test_each_refresh_reads_new_cash_and_caller_changes_do_not_poison_cache(self):
        @historical_cutoff.reuse_cutoff_projection
        def report():
            first = historical_cutoff.load_cutoff(self.filters[0])
            first['detail'][0]['application_pending_usd'] = 999
            first['warnings'].append('Presentation only')
            return historical_cutoff.load_cutoff(self.filters[0])

        first = report()
        self.assertEqual(first['detail'][0]['application_pending_usd'], 40)
        self.assertEqual(first['warnings'], [])
        self.deposit['allocation_detail'][0]['importe_usd'] = 70
        self.deposit['amount_usd'] = 90
        second = report()
        self.assertEqual(second['detail'][0]['application_pending_usd'], 30)
        self.assertEqual(self.context_read.call_count, 2)

    def test_cutoff_bridge_and_detail_require_separate_evidence(self):
        @historical_cutoff.reuse_cutoff_projection
        def report():
            historical_cutoff.load_cutoff(self.filters[0])
            historical_cutoff.load_cutoff(dict(self.filters[0], cutoff_date='2026-03-01'))
            historical_cutoff.load_cutoff(self.filters[0], include_bridge=True)
            with patch('credinomina_reconciliation.deposit_detail_classification.attach_details'):
                historical_cutoff.load_cutoff(dict(self.filters[0], _include_deposit_details=True))
        report()
        self.assertEqual(self.context_read.call_count, 4)

    def test_user_change_and_permission_denial_never_reuse_another_users_access(self):
        @historical_cutoff.reuse_cutoff_projection
        def report():
            historical_cutoff.load_cutoff(self.filters[0])
            frappe.session.user = 'SECOND'
            historical_cutoff.load_cutoff(self.filters[0])
            self.allowed.return_value = False
            historical_cutoff.load_cutoff(self.filters[0])
        with self.assertRaises(frappe.PermissionError):
            report()
        self.assertEqual(self.context_read.call_count, 2)
        self.assertIsNone(historical_cutoff._report_projections.get())

    def test_filters_and_validation_remain_effective_on_a_cached_cutoff(self):
        cases = [dict(self.filters[0], search='absent'), dict(self.filters[0], from_date='2026-04-01'),
                 dict(self.filters[0], component='deposit_pending_usd'), dict(self.filters[0], status='Absent')]
        self.assertTrue(all(not value['detail'] for value in self.run_report([self.filters[0], *cases])[1:]))
        self.context_read.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'Desde'):
            self.run_report([self.filters[0], dict(self.filters[0], from_date='2026-09-01')])
        self.assertIsNone(historical_cutoff._report_projections.get())
