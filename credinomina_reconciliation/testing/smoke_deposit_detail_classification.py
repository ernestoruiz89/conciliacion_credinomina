"""Rollback-only end-to-end report test for classified deposit detail."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa.estado_de_cuenta_por_empresa import execute


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    marker = 'DETAIL-CLASS-' + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            reason = frappe.get_doc(dict(doctype='CN Difference Reason', reason_name=marker, treatment='A Favor Cliente')).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2026-05-01', reconciliation_mode='Historica')).insert()
            amounts = [257.79, 25.42, 28.94, 36.34, 38.39, 3383.17]
            pending = [60.26, .01, 3.58, .01, .01, 0]
            parent = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name, currency='USD',
                source_file=f'/private/files/{marker}.csv', historical_period=period.name, status='Importado',
                rows=[dict(source_key=f'{marker}-{i}', event_type='Aplicacion', event_date='2026-05-15', currency='USD',
                    amount=round(amount-pending[i], 2), effective=1, historical_period=period.name,
                    accounting_source_key=f'{marker}-APP-{i}', source_account='160209013004',
                    source_currency='USD', source_debit=round(amount-pending[i], 2), source_credit=0,
                    match_status='Conciliado', client_name=f'Cliente {i}', client_number=str(i+1), loan_number=f'{i+1}-1')
                    for i, amount in enumerate(amounts)])).insert()
            # Represent an existing submitted deposit and its stored allocations;
            # report reads must never replay matching or change these values.
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', name=marker, employer=employer.name,
                docstatus=1, deposit_date='2026-05-15', deposit_currency='USD', deposit_amount=3770.04, amount_usd=3770.04,
                accounting_source_key=marker+'-BANK', source_date='2026-05-15', source_currency='USD',
                source_account='160209013004', source_debit=0, source_credit=3709.78,
                allocated_usd=3706.18, justified_surplus_usd=0, unclassified_usd=63.86, unallocated_usd=63.86,
                result='Revisar detalle', detail_status='Revisar filas'))
            deposit.db_insert()
            entries = []
            for i, amount in enumerate(amounts):
                row = frappe.get_doc(dict(doctype='CN Remittance Detail', name=f'{marker}-{i}',
                    parent=deposit.name, parenttype=deposit.doctype, parentfield='detail_rows', docstatus=1, idx=i+1,
                    row_kind='Cliente', employer=employer.name, client_name=f'Cliente {i}', client_number=str(i+1),
                    loan_number=f'{i+1}-1', amount_usd=amount, deducted_usd=amount, linked_usd=round(amount-pending[i], 2),
                    pending_usd=pending[i], difference_reason=reason.name if pending[i] else '', match_status='Revisar'))
                row.db_insert()
                frappe.db.set_value('CN Source Row', parent.rows[i].name,
                    'historical_remitted_usd', round(amount-pending[i], 2), update_modified=False)
                entries.append(dict(tipo='Aplicacion historica', aplicacion_id=parent.rows[i].name,
                    periodo=period.name, importe_usd=round(amount-pending[i], 2), fila_detalle=row.name))
            frappe.get_doc(dict(doctype='CN Remittance Detail', name=f'{marker}-COMPANY', parent=deposit.name,
                parenttype=deposit.doctype, parentfield='detail_rows', docstatus=1, idx=7,
                row_kind='Diferencia documentada', amount_usd=-.01, difference_amount_usd=-.01,
                difference_core_pending_usd=.01, difference_reason=reason.name, match_status='Diferencia documentada')).db_insert()
            frappe.db.set_value(deposit.doctype, deposit.name, 'allocation_detail', json.dumps(entries))
            before = frappe.as_json(deposit.reload().as_dict())
            for cutoff in (None, '2026-08-31'):
                scope = dict(employer=employer.name, statement_scope='Gestión', **({'cutoff_date': cutoff} if cutoff else {}))
                summary = execute(scope)[1][0]
                assert summary['client_credit_usd'] == -63.87, summary
                assert summary['deposit_pending_usd'] == .01, summary
                assert summary['company_receivable_usd'] == summary['employee_receivable_usd'] == 0
                assert summary['management_net_usd'] == -63.86, summary
                detail = execute(dict(scope, view_mode='Detalle', component='client_credit_usd'))[1]
                assert [row['statement_amount_usd'] for row in detail[:-1]] == [-60.26, -.01, -3.58, -.01, -.01], detail
                assert detail[-1]['statement_amount_usd'] == -63.87
                calculated = execute(dict(scope, statement_scope='Todos'))[1][0]
                assert calculated['deposit_documentation_adjustment_usd'] == 60.26, calculated
                assert calculated['management_net_usd'] == -63.86, calculated
                assert calculated['calculated_balance_usd'] == calculated['core_balance_usd'] == -3.60, calculated
                assert calculated['reconciliation_difference_usd'] == 0, calculated
            assert frappe.as_json(deposit.reload().as_dict()) == before
            return dict(client_credit=63.87, company_difference=.01, net=-63.86, historical=True, no_writes=True, rolled_back=True)
    finally:
        frappe.db.rollback()
