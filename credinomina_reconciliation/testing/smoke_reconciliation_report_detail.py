"""Rollback-only: pending receipts are separate; saved reconciliation is unchanged."""
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
                    deducted_usd=40, amount_usd=40, comments='Comentario documentado de prueba')])).insert()
            deposit.submit()
            reconcile_deposit(deposit)
            assert money(deposit.reload().allocated_usd) == 20, dict(allocated=deposit.allocated_usd,
                result=deposit.result, reason=deposit.detail_rows[0].match_reason)
            assert money(deposit.unclassified_usd) == 20
            documents = [deposit.reload(), source.reload(), period.reload()]
            before = [document_state(doc) for doc in documents]
            from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion.resumen_de_conciliacion import execute
            for view in ('Resumen', 'Detalle'):
                columns, rows, *_ = execute(dict(period=period.name, view=view))
                assert len(rows) == (2 if view == 'Detalle' else 1), rows
                row = rows[0]
                assert row['application_usd'] == row['deposit_usd'] == 20 and row['open_usd'] == 0, row
                assert row['cash_control'] == 'Cubierta' and not row['has_difference']
                assert not {'detail_received_usd', 'detail_unassigned_usd'} & {column['fieldname'] for column in columns}
                differences = execute(dict(period=period.name, view=view, only_differences=1))[1]
                assert len(differences) == (1 if view == 'Detalle' else 0), differences
                if view == 'Detalle':
                    assert row['client_name'] == 'Juan prueba'
                    pending = rows[1]
                    assert pending['client_name'] == 'Juan prueba' and pending['deposit_usd'] == 20, pending
                    assert pending['application_usd'] == pending['open_usd'] == 0, pending
                    assert pending['source_document'] == deposit.name and pending['has_difference'], pending
                assert before == [document_state(doc.reload()) for doc in documents]
            from credinomina_reconciliation.conciliacion_credinomina.report.conciliacion_por_cliente_y_periodo.conciliacion_por_cliente_y_periodo import execute as client_report
            columns, client_rows, message, chart, totals, skip_total = client_report(dict(period=period.name))
            assert len(client_rows) == 1, client_rows
            client_row = client_rows[0]
            assert (client_row['bank_usd'], client_row['applied_usd'], client_row['pending_usd']) == (40, 20, 20)
            assert 'Comentario documentado de prueba' in client_row['comment']
            assert 'reembolso' not in client_row['comment'].casefold()
            assert client_row['state'] == 'Revisar' and client_row['row_number'] == 1
            assert [total['value'] for total in totals] == [40, 20, 20]
            assert before == [document_state(doc.reload()) for doc in documents]
            unlinked = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2026-08-31', deposit_reference=marker + '-UNLINKED',
                deposit_currency='USD', deposit_amount=7)).insert()
            unlinked.submit()
            from credinomina_reconciliation.manual_complementary_policy import imported_item
            with imported_item(marker + '-CORE'):
                complement = frappe.get_doc(dict(doctype='CN Complementary Item', employer=company.name,
                    posting_date='2026-08-31', currency='USD', amount=3, accounting_source_key=marker + '-CORE',
                    source_currency='USD', source_credit=3, source_debit=0, source_date='2026-08-31',
                    category='Ajuste de conciliación', review_action='Partida de depósito', amount_reviewed=1,
                    subcategory='Otro ajuste sin CxC',
                    reference=marker + '-CORE',
                    client_number=client.client_number, client_name=client.client_name, loan_number='999999-1',
                    description='Remanente del core sin depósito de prueba')).insert()
                complement.submit()
            documents.extend([unlinked.reload(), complement.reload()])
            before = [document_state(doc) for doc in documents]
            pending_rows = execute(dict(period=period.name, view='Detalle', only_differences=1))[1]
            assert len(pending_rows) == 3, pending_rows
            bank_line = next(row for row in pending_rows if row['source_document'] == unlinked.name)
            comp_line = next(row for row in pending_rows if row['source_document'] == complement.name)
            assert bank_line['deposit_usd'] == 7 and bank_line['name'] is None, bank_line
            assert comp_line['cash_adjustment_usd'] == -3 and comp_line['name'] is None, comp_line
            assert comp_line['client_name'] == client.client_name and comp_line['open_usd'] == 0, comp_line
            beneficiary = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker + '-CBC', employer_code=marker + '-CBC')).insert()
            company.append('paying_for', dict(employer=beneficiary.name))
            company.save()
            frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=beneficiary.name,
                payroll_month='2026-08-01', reconciliation_mode='Historica')).insert()
            beneficiary_deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=beneficiary.name,
                deposit_date='2026-08-31', deposit_reference=marker + '-CBC', deposit_currency='USD', deposit_amount=5)).insert()
            beneficiary_deposit.submit()
            documents.append(beneficiary_deposit.reload())
            before = [document_state(doc.reload()) for doc in documents]
            from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa.estado_de_cuenta_por_empresa import execute as company_report
            for report, extra in ((execute, dict(view='Resumen')), (company_report, dict(view_mode='Resumen', statement_scope='Gestión'))):
                for include, expected in ((0, {company.name}), (1, {company.name, beneficiary.name})):
                    result = report(dict(extra, employer=company.name, include_paying_companies=include))[1]
                    assert {row['employer'] for row in result} == expected, result
                    if report is company_report:
                        amounts = {row['employer']: row['management_net_usd'] for row in result}
                        assert amounts[company.name] == -30, amounts
                        if include:
                            assert amounts[beneficiary.name] == -5, amounts
            assert before == [document_state(doc.reload()) for doc in documents]
            second_period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2026-07-01', reconciliation_mode='Historica')).insert()
            second_import = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                historical_backfill=1, historical_period=second_period.name, status='Importado', currency='USD',
                source_file=f'/private/files/{marker}-SECOND.csv', rows=[dict(source_key=marker + '-SECOND',
                    event_type='Aplicacion', event_date='2026-07-15', currency='USD', amount=20, amount_usd=20,
                    effective=1, client=client.name, client_number=client.client_number, client_name=client.client_name,
                    loan_number='888888-1', processing_route='Historica', historical_period=second_period.name)])).insert()
            _reconcile_sources(company.name, preserve_deposits=True)
            documents.extend([second_period.reload(), second_import.reload()])
            before = [document_state(doc.reload()) for doc in documents]
            for show_month, show_period in ((0, 0), (1, 0), (0, 1), (1, 1)):
                columns, client_detail, _, _, _, skip_total = execute(dict(employer=company.name,
                    view='Detalle por Cliente', show_month=show_month, show_period=show_period, only_differences=1))
                fields = {column['fieldname'] for column in columns}
                assert ('name' in fields) == bool(show_period) and ('payroll_month' in fields) == bool(show_month), columns
                assert len(client_detail) == 2, client_detail
                identified = [row for row in client_detail if row['client_number'] == client.client_number]
                assert len(identified) == 1, identified
                client_line = identified[0]
                assert client_line['application_usd'] == 40 and client_line['deposit_usd'] == 20, client_line
                assert client_line['open_usd'] == 20 and client_line['deposit_unallocated_usd'] == 20, client_line
                assert client_line['complementary_unallocated_usd'] == 3 and client_line['cash_adjustment_usd'] == 0, client_line
                assert '888888-1' in client_line['loan_number'] and '999999-1' in client_line['loan_number'], client_line
                assert period.name in client_line['name'] and second_period.name in client_line['name'], client_line
                assert not skip_total
                assert before == [document_state(doc.reload()) for doc in documents]
            return dict(detail_received_usd=40, assigned_usd=20, unassigned_usd=20,
                        application_pending_usd=0, pending_detail_line_verified=True, client_period_report_verified=True,
                        unlinked_deposit_and_complement_verified=True, payer_filter_in_both_reports_verified=True,
                        consolidated_client_across_periods_verified=True, optional_client_columns_verified=True,
                        saved_reconciliation_unchanged=True,
                        rollback=True)
    finally:
        frappe.db.rollback()
