import json
import unittest
from collections import defaultdict
from unittest.mock import patch

import frappe

from credinomina_reconciliation.application_candidate_context import CandidateContext, cash_journals
from credinomina_reconciliation.application_adjustments import cash_coverage


class CandidateCashReadTests(unittest.TestCase):
    def test_paged_preview_matches_individual_coverage_with_shared_claims_and_reservations(self):
        rows = [frappe._dict(name=f'A{i}', amount=100, currency='USD', historical_remitted_usd=0)
                for i in range(25)]
        rows[0].historical_remitted_usd = 35
        rows[1].collection_row_id = 'COL'
        rows[1].application_allocation_detail = '[{"collection_row_id":"COL","amount_usd":100}]'
        collection = frappe._dict(name='COL', parent='P', row_key='KEY', applied_usd=100)
        targets = [frappe._dict(name='T1', parent='D1', historical_application='A0', amount_usd=40),
                   frappe._dict(name='T2', parent='D2', period='P', row_key='KEY', amount_usd=50),
                   frappe._dict(name='T3', parent='DRAFT', historical_application='A2', amount_usd=20)]
        deposits = [frappe._dict(name='D1', allocation_detail=json.dumps([
            dict(tipo='Aplicacion historica', aplicacion_id='A0', importe_usd=30)])),
            frappe._dict(name='D2', allocation_detail=json.dumps([
                dict(tipo='Cobranza', periodo='P', fila_id='KEY', importe_usd=60),
                dict(tipo='Cobranza', periodo='P', fila_id='KEY', importe_usd=-10)])),
            frappe._dict(name='OTHER-COMPANY', allocation_detail=json.dumps([
                dict(tipo='Aplicacion historica', aplicacion_id='A0', importe_usd=5),
                dict(tipo='Aplicacion historica', aplicacion_id='A0-OTHER', importe_usd=99),
                dict(tipo='Partida complementaria', aplicacion_id='A3', importe_usd=99),
                dict(observacion='A3', importe_usd=99)]))]
        funded = defaultdict(int, A3=15)

        def query(doctype, **kwargs):
            if doctype == 'CN Remittance Allocation':
                return deposits
            if doctype == 'CN Remittance Target':
                filters = kwargs['filters']
                if 'historical_application' in filters:
                    value = filters['historical_application']
                    wanted = set(value[1]) if isinstance(value, list) else {value}
                    return [target for target in targets if target.historical_application in wanted]
                return [target for target in targets if target.period == 'P']
            return []

        context = CandidateContext.__new__(CandidateContext)
        context.rows = rows
        context.collections = {'COL': collection}
        context.reserved = defaultdict(int)
        with patch.object(frappe, 'get_all', side_effect=query) as queries, \
                patch.object(frappe.db, 'get_value', return_value=collection), \
                patch('credinomina_reconciliation.credit_application.funding_totals', return_value=funded):
            context._load_cash()
            reads = [call for call in queries.call_args_list if call.args[0] == 'CN Remittance Allocation']
            self.assertEqual(len(reads), 1)
            self.assertEqual(reads[0].kwargs['limit_page_length'], 500)
            for row in rows:
                expected = cash_coverage(row)
                actual = cash_coverage(row, context=context)
                for field in ('adjustable_usd', 'protected_usd'):
                    self.assertEqual(actual[field], expected[field], (row.name, field))
        self.assertEqual(context.coverage(rows[0])['adjustable_usd'], 55)
        self.assertEqual(context.coverage(rows[1])['adjustable_usd'], 50)
        self.assertEqual(context.coverage(rows[2])['adjustable_usd'], 80)
        self.assertEqual(context.coverage(rows[3])['adjustable_usd'], 85)

    def test_large_reads_advance_pages_without_company_date_or_target_filter(self):
        first = [frappe._dict(name=f'D{i:04}', allocation_detail='[]') for i in range(500)]
        last = [frappe._dict(name='LAST', allocation_detail='[]')]
        with patch.object(frappe, 'get_all', side_effect=[first, last]) as query:
            result = list(cash_journals({f'A{i}' for i in range(542)}))
        self.assertEqual(len(result), 501)
        self.assertEqual(query.call_count, 2)
        self.assertEqual(query.call_args.kwargs['filters'], {
            'docstatus': 1, 'name': ['>', 'D0499'], 'allocation_detail': ['not in', ['', '[]']]})
        self.assertNotIn('or_filters', query.call_args.kwargs)

    def test_small_reads_keep_literal_identifiers_and_empty_selection_skips_database(self):
        with patch.object(frappe, 'get_all', return_value=[]) as query:
            self.assertEqual(list(cash_journals([])), [])
            query.assert_not_called()
            self.assertEqual(list(cash_journals(['A_%\\B'])), [])
            self.assertEqual(query.call_args.kwargs['or_filters'], [
                ['allocation_detail', 'like', '%A\\_\\%\\\\B%']])
            self.assertEqual(query.call_args.kwargs['filters'], {'docstatus': 1})
