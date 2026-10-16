"""Reimport one cut and refresh evidence without changing reconciled money."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.credit_portfolio import PORTFOLIO_FIELDS, enrich_accounting_records
from credinomina_reconciliation.remittance_portfolio import FIELDS
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_credit_portfolio_snapshot import cn_credit_portfolio_snapshot as importer
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_remittance_allocation.cn_remittance_allocation import _apply_remittance_detail
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit


def _financial_document(doc, table, permitted):
    values = doc.as_dict()
    for field in ('modified', 'modified_by', '_comments', '_seen'):
        values.pop(field, None)
    for row in values.get(table) or []:
        for field in permitted:
            row.pop(field, None)
    return frappe.as_json(values)


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    marker = 'REFRESH-' + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, 'enqueue'):
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker,
                employer_code=marker, payroll_frequency='Mensual')).insert()
            client = frappe.get_doc(dict(doctype='CN Client', employer=employer.name,
                client_number=marker, client_name=marker)).insert()
            cut = frappe.get_doc(dict(doctype='CN Credit Portfolio Snapshot',
                source_file=f'/private/files/{marker}.xlsx')).insert()

            def load(states, generation):
                records = [dict(report_date='2093-11-30', source_row=i + 2,
                    credit_number=f'{marker}-{loan}-1', credit_status=status,
                    client_number_core=marker, client_name=f'Nombre cartera {generation}',
                    employer_text=employer.name, is_convenio='Sí')
                    for i, (loan, status) in enumerate(states.items())]
                with patch.object(importer, '_attached_file', return_value=(
                    frappe._dict(file_name='refresh.xlsx'), f'{marker}-{generation}'.encode())), \
                     patch.object(importer, 'parse_credit_portfolio', return_value=records):
                    result = importer.import_portfolio_snapshot(cut.name)
                cut.name = result['snapshot_name']
                cut.reload()
                return result

            load({'KEPT': 'Corriente', 'REMOVED': 'Vencido'}, 1)
            original_name = cut.name
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2025-07-01', reconciliation_mode='Historica')).insert()
            records = [dict(source_key=f'{marker}-{loan}', event_type='Aplicacion', event_date='2025-07-15',
                loan_number=f'{marker}-{loan}-1', client=client.name, client_number=marker,
                client_name='Nombre del movimiento', currency='USD', amount=10, amount_usd=10,
                historical_period=period.name) for loan in ('KEPT', 'REMOVED', 'ADDED')]
            enrich_accounting_records(records, cut.name, employer.name, register_clients=False)
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                source_file=f'/private/files/{marker}.csv', status='Importado', currency='USD',
                portfolio_snapshot=cut.name, historical_period=period.name, rows=records)).insert()
            _reconcile_sources(employer.name, preserve_deposits=True)
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_date='2025-07-31', deposit_reference=marker, deposit_currency='USD', deposit_amount=30,
                detail_file=f'/private/files/{marker}-detail.csv',
                detail_periods=[dict(period=period.name)])).insert()
            detail = [dict(source_row=i + 2, loan_number=f'{marker}-{loan}-1', client_number=marker,
                client_name=client.client_name, deducted_usd=10) for i, loan in enumerate(('KEPT', 'REMOVED', 'ADDED'))]
            _apply_remittance_detail(deposit, detail, b'detail', f'/private/files/{marker}-detail.csv')
            deposit.reload()
            deposit.submit()
            reconcile_deposit(deposit)
            source.reload()
            deposit.reload()
            period.reload()
            assert deposit.allocated_usd == 30, (deposit.allocated_usd, deposit.detail_status)
            # Exercise the same protection for a closed period. Only the flag
            # is set here; closing validations belong to their dedicated tests.
            frappe.db.set_value(period.doctype, period.name, 'status', 'Cerrado')
            period.reload()
            source_before = _financial_document(source, 'rows', PORTFOLIO_FIELDS)
            deposit_before = _financial_document(deposit, 'detail_rows', FIELDS)
            period_before = frappe.as_json(period.as_dict())
            source_ids = [row.name for row in source.rows]
            detail_ids = [row.name for row in deposit.detail_rows]

            result = load({'KEPT': 'Cancelado', 'ADDED': 'Saneado'}, 2)
            assert cut.name == original_name
            assert len(cut.rows) == 2
            assert result['updated_source_rows'] == 3, result
            assert result['updated_deposit_rows'] == 3, result
            source.reload()
            deposit.reload()
            period.reload()
            assert [row.portfolio_credit_status for row in source.rows] == ['Cancelado', '', 'Saneado']
            assert [row.portfolio_credit_status for row in deposit.detail_rows] == ['Cancelado', 'No Identificado', 'Saneado']
            assert [row.name for row in source.rows] == source_ids
            assert [row.name for row in deposit.detail_rows] == detail_ids
            assert _financial_document(source, 'rows', PORTFOLIO_FIELDS) == source_before
            assert _financial_document(deposit, 'detail_rows', FIELDS) == deposit_before
            assert frappe.as_json(period.as_dict()) == period_before

            repeated = load({'KEPT': 'Cancelado', 'ADDED': 'Saneado'}, 2)
            assert repeated['unchanged']
            assert repeated['updated_source_rows'] == repeated['updated_deposit_rows'] == 0
            # Repair stale metadata even when the attached workbook is unchanged.
            frappe.db.set_value('CN Source Row', source_ids[0], 'portfolio_credit_status', 'STALE', update_modified=False)
            frappe.db.set_value('CN Remittance Detail', detail_ids[0], 'portfolio_credit_status', 'STALE', update_modified=False)
            repaired = load({'KEPT': 'Cancelado', 'ADDED': 'Saneado'}, 2)
            assert repaired['unchanged'] and repaired['updated_source_rows'] == repaired['updated_deposit_rows'] == 1

            # A dependent failure must roll back the replaced cut and updates
            # already written to applications in the same request transaction.
            frappe.db.savepoint('portfolio_refresh_failure')
            old_hash = cut.file_hash
            with patch('credinomina_reconciliation.portfolio_refresh.update_detail_portfolio', side_effect=RuntimeError('dependent failure')):
                try:
                    load({'KEPT': 'Vencido', 'ADDED': 'Vencido'}, 3)
                except RuntimeError:
                    frappe.db.rollback(save_point='portfolio_refresh_failure')
                else:
                    raise AssertionError('Expected dependent failure')
            cut.reload()
            source.reload()
            deposit.reload()
            assert cut.file_hash == old_hash
            assert source.rows[0].portfolio_credit_status == deposit.detail_rows[0].portfolio_credit_status == 'Cancelado'
            assert _financial_document(source, 'rows', PORTFOLIO_FIELDS) == source_before
            assert _financial_document(deposit, 'detail_rows', FIELDS) == deposit_before
            return dict(same_snapshot=True, added_and_removed_credits=True, refreshed_sources=3,
                refreshed_submitted_detail=3, closed_period_preserved=True, reconciled_money_preserved=True,
                unchanged_file_repairs_metadata=True, repeat_idempotent=True, atomic_rollback=True)
    finally:
        frappe.db.rollback()
