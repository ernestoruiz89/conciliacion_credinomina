"""Real Frappe report: shared payer, fresh cutoffs and bounded reads; rollback only."""
import json
from time import perf_counter
from unittest.mock import Mock, patch

import frappe

from credinomina_reconciliation import historical_cutoff
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as report


def run(count=1000, company_count=8):
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    assert count > 1 and company_count >= 3
    marker = 'CUT-PERF-' + frappe.generate_hash(length=8)
    companies = [f'{marker}-E{i}' for i in range(company_count)]
    measurements = []
    try:
        for n, company in enumerate(companies):
            period, parent = f'{marker}-P{n}', f'{marker}-I{n}'
            frappe.get_doc(dict(doctype='CN Employer', name=company, employer_name=company, employer_code=company)).db_insert()
            frappe.get_doc(dict(doctype='CN Reconciliation Period', name=period, employer=company,
                payroll_month='2026-03-01', reconciliation_mode='Historica')).db_insert()
            frappe.get_doc(dict(doctype='CN Accounting Import', name=parent, employer=company,
                currency='USD', status='Importado', historical_backfill=1, historical_period=period)).db_insert()
            fields = ['name', 'parent', 'parenttype', 'parentfield', 'idx', 'event_type', 'effective', 'currency', 'amount',
                'event_date', 'historical_period', 'match_status', 'client_number', 'client_name', 'source_account',
                'source_currency', 'source_debit', 'source_credit', 'accounting_source_key', 'source_row']
            values, ledger = [], []
            for i in range(count):
                key = f'{parent}-{i}'
                values.append([key, parent, 'CN Accounting Import', 'rows', i + 1, 'Aplicacion', 1, 'USD', 100,
                    '2026-03-15', period, 'Conciliado', str(i), f'Cliente {i}', '160209013004', 'USD', 100, 0, key, i + 1])
                # A pays 20 of B's first application; company identities stay separate.
                amount = 40 if (n, i) in {(0, count - 1), (1, 0)} else 60
                ledger.append(dict(tipo='Aplicacion historica', aplicacion_id=key, periodo=period, importe_usd=amount))
            if n == 0:
                ledger.append(dict(tipo='Aplicacion historica', aplicacion_id=f'{marker}-I1-0',
                                   periodo=f'{marker}-P1', importe_usd=20))
            frappe.db.bulk_insert('CN Source Row', fields=fields, values=values)
            cash = sum(row['importe_usd'] for row in ledger)
            frappe.get_doc(dict(doctype='CN Remittance Allocation', name=f'{marker}-D{n}', employer=company, docstatus=1,
                deposit_date='2026-03-20', amount_usd=cash, allocated_usd=cash, deposit_currency='USD', deposit_amount=cash,
                source_currency='USD', source_debit=0, source_credit=cash, source_account='160209013004',
                source_date='2026-03-20', accounting_source_key=f'{marker}-BANK{n}', allocation_detail=json.dumps(ledger))).db_insert()
        for i, company in enumerate(companies[1:3]):
            frappe.get_doc(dict(doctype='CN Paying Employer', name=f'{marker}-PAY{i}', parent=companies[0],
                parenttype='CN Employer', parentfield='paying_for', idx=i + 1, employer=company)).db_insert()
        filters = dict(employer=companies[0], cutoff_date='2026-08-31', include_paying_companies=1)
        report.execute(filters)  # Warm metadata before timing either path.
        sql = frappe.db.sql

        def measure(label, scope, reuse):
            queries = []
            def read(query, *args, **kwargs):
                text = str(query).strip().lower()
                assert not text.startswith(('insert ', 'update ', 'delete ', 'replace ')), 'Report attempted a write'
                queries.append(text)
                return sql(query, *args, **kwargs)
            cache = historical_cutoff._report_projections if reuse else Mock(get=Mock(return_value=None))
            with patch.object(frappe.db, 'sql', side_effect=read), \
                    patch.object(historical_cutoff, '_report_projections', cache), \
                    patch.object(historical_cutoff, 'reconstruct', wraps=historical_cutoff.reconstruct) as projections:
                started = perf_counter()
                result = report.execute(scope)
                seconds = perf_counter() - started
            measurements.append(dict(run=label, seconds=round(seconds, 3), queries=len(queries), projections=projections.call_count))
            return result

        baseline = measure('without_reuse', filters, False)
        optimized = measure('with_reuse', filters, True)
        assert optimized == baseline, 'Columns, balances, states or warnings changed'
        assert measurements[-1]['projections'] == 1 and measurements[-2]['projections'] == 3
        assert measurements[-1]['queries'] < measurements[-2]['queries']
        amounts = {row['employer']: row['management_net_usd'] for row in optimized[1]}
        assert amounts == {companies[0]: count * 40 + 20, companies[1]: count * 40, companies[2]: count * 40}
        detail_scope = dict(filters, view_mode='Detalle', statement_scope='Gestión')
        assert measure('detail_reuse', detail_scope, True) == measure('detail_without_reuse', detail_scope, False)
        bridge_scope = dict(filters, view_mode='Conciliación')
        assert measure('bridge_reuse', bridge_scope, True) == measure('bridge_without_reuse', bridge_scope, False)
        # A new execution must not reuse a previous date or its applications.
        early = measure('before_applications', dict(filters, cutoff_date='2026-03-01'), True)
        assert early[1] == []
        refreshed = measure('refresh', filters, True)
        assert refreshed == optimized and measurements[-1]['projections'] == 1
        assert historical_cutoff._report_projections.get() is None
        return dict(applications=count * company_count, measurements=measurements,
            identical_results=True, shared_payer=True, read_only=True, rolled_back=True)
    finally:
        frappe.db.rollback()
