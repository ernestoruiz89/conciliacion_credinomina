"""Rollback-only: a US$40 deposit detail covering US$20 stays visible in both views."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.rounding import money


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'), \
                patch('frappe.model.rename_doc.sync_user_settings'):
            marker = 'REPORT-EXCESS-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype='CN Client', employer=company.name,
                client_number=marker, client_name='Juan prueba')).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2026-08-01', reconciliation_mode='Historica')).insert()
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                historical_backfill=1, historical_period=period.name, status='Importado', currency='USD',
                source_file=f'/private/files/{marker}.csv', rows=[dict(source_key=marker, event_type='Aplicacion',
                    event_date='2026-08-15', currency='USD', amount=20, amount_usd=20, effective=1,
                    client=client.name, client_number=client.client_number, client_name=client.client_name,
                    loan_number='999999-1', processing_route='Historica', historical_period=period.name)])).insert()
            from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
            _reconcile_sources(company.name, preserve_deposits=True)
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2026-09-01', deposit_reference=marker, deposit_currency='USD', deposit_amount=40,
                detail_file=f'/private/files/{marker}.xlsx', detail_source_file=f'/private/files/{marker}.xlsx', detail_hash=marker,
                apply_fifo=1, detail_periods=[dict(period=period.name)],
                detail_rows=[dict(source_row=2, employer=company.name, client=client.name,
                    client_number=client.client_number, client_name=client.client_name, loan_number='999999-1',
                    deducted_usd=40, amount_usd=40)])).insert()
            deposit.submit()
            reconcile_deposit(deposit)
            assert money(deposit.reload().allocated_usd) == 20, dict(allocated=deposit.allocated_usd,
                result=deposit.result, reason=deposit.detail_rows[0].match_reason)
            assert money(deposit.unclassified_usd) == 20
            documents = [deposit.reload(), source.reload(), period.reload()]
            before = [document_state(doc) for doc in documents]
            from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion.resumen_de_conciliacion import execute
            for view in ('Resumen', 'Detalle'):
                columns, rows, *_ = execute(dict(period=period.name, view=view, only_differences=1))
                assert len(rows) == 1, rows
                row = rows[0]
                assert row['detail_received_usd'] == 40 and row['detail_unassigned_usd'] == 20, row
                assert row['application_usd'] == row['deposit_usd'] == 20 and row['open_usd'] == 0, row
                assert row['cash_control'] == 'Recibido sin distribuir'
                assert {'detail_received_usd', 'detail_unassigned_usd'} <= {column['fieldname'] for column in columns}
                if view == 'Detalle':
                    assert row['client_name'] == 'Juan prueba'
                assert before == [document_state(doc.reload()) for doc in documents]
            return dict(detail_received_usd=40, assigned_usd=20, unassigned_usd=20,
                        application_pending_usd=0, visible_in_both_views=True, saved_reconciliation_unchanged=True,
                        rollback=True)
    finally:
        frappe.db.rollback()
