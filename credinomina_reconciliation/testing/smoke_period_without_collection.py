"""Rollback-only proof that off-payroll periods close only with settled applications."""
from unittest.mock import patch
import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as periods
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.period_pending import get_period_pending
from credinomina_reconciliation.testing.smoke_client_credit import must_fail
from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion.resumen_de_conciliacion import execute as report


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'NO-COLLECTION-' + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker,
                employer_code=marker, payroll_frequency='Mensual')).insert()
            client = frappe.get_doc(dict(doctype='CN Client', employer=employer.name,
                client_name=marker, client_number=marker)).insert()
            base = dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2026-09-01', reconciliation_mode='Operativa', collection_cycle='Mensual')
            normal = frappe.get_doc(base).insert()
            period = frappe.get_doc(dict(base, no_collection=1)).insert()
            assert normal.name != period.name
            must_fail(lambda: periods.close_period(period.name))
            must_fail(lambda: periods.import_collection(period.name))
            must_fail(lambda: periods.reconcile_first(period.name))
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                source_file='/private/files/' + marker + '.csv', currency='USD', status='Importado',
                historical_period=period.name, rows=[dict(event_type='Aplicacion', event_date='2026-09-30',
                    source_key=marker, client=client.name, client_name=marker, client_number=marker,
                    loan_number='109136-1', effective=1, currency='USD', amount=100, amount_usd=100,
                    historical_period=period.name, processing_route='Operativa')])).insert()
            _reconcile_sources(employer.name, preserve_deposits=True)
            source.reload(); period.reload()
            assert period.pending_usd == 100
            assert source.rows[0].quality_status == 'No aplica: período sin cobranza'
            issues = get_period_pending(period.name)['rows']
            assert len(issues) == 1 and issues[0]['pending'] == 100, issues
            assert issues[0]['status'] != 'Conciliación 1 pendiente'
            must_fail(lambda: periods.close_period(period.name))
            path = '/private/files/' + marker + '.xlsx'
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_date='2026-10-01', deposit_reference=marker, deposit_currency='USD', deposit_amount=100,
                detail_periods=[dict(period=period.name)], detail_file=path, detail_source_file=path, detail_hash=marker,
                detail_rows=[dict(client=client.name, client_number=marker, client_name=marker,
                    loan_number='109136-1', deducted_usd=100, source_row=2)])).insert()
            deposit.submit(); reconcile_deposit(deposit)
            period.reload(); deposit.reload()
            assert period.pending_usd == 0 and period.remitted_total_usd == 100
            assert not get_period_pending(period.name)['rows']
            original_cash = deposit.allocation_detail
            periods.close_period(period.name)
            period.reload(); deposit.reload(); normal.reload()
            assert period.status == 'Cerrado' and not period.collection_rows
            summary = report({'period': period.name})[1]
            detail = report({'period': period.name, 'view': 'Detalle'})[1]
            assert len(summary) == len(detail) == 1
            for row in [summary[0], detail[0]]:
                assert row['name'] == period.name and row['employer'] == employer.name
                assert row['collection_control'] == 'No aplica' and row['collection_usd'] is None
                assert row['application_usd'] == row['deposit_usd'] == 100
                assert row['deposit_difference_usd'] == row['open_usd'] == 0
            assert detail[0]['client_number'] == client.name and detail[0]['loan_number'] == '109136-1'
            assert not report({'period': period.name, 'view': 'Detalle', 'only_differences': 1})[1]
            assert deposit.allocation_detail == original_cash
            assert normal.status != 'Cerrado'
            period.no_collection = 0
            must_fail(lambda: period.save())
            return dict(no_collection_required=True, unpaid_close_rejected=True,
                        paid_close_accepted=True, first_stage_not_pending=True,
                        regular_period_preserved=True, original_cash_preserved=True, report_views_verified=True)
    finally:
        frappe.db.rollback()
