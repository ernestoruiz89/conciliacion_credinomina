"""Rejected row instructions must never move cash; legacy excess cannot close."""
import json
from copy import deepcopy
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'AUDIT-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2025-04-01', reconciliation_mode='Historica')).insert()
            clients = [frappe.get_doc(dict(doctype='CN Client', employer=company.name,
                client_name=marker + suffix, client_number=marker + suffix)).insert() for suffix in ('A', 'B')]
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                source_file=f'/private/files/{marker}.csv', status='Importado', currency='USD', historical_backfill=1,
                rows=[dict(source_key=f'{marker}-{index}', event_type='Aplicacion', event_date='2025-04-30',
                    currency='USD', amount=100, amount_usd=100, effective=1, historical_period=period.name,
                    client=client.name, client_number=client.client_number, loan_number=f'{index + 10001}-1')
                    for index, client in enumerate(clients)])).insert()
            engine._reconcile_sources(company.name)
            source.reload()
            wrong = clients[1]
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2025-04-30', deposit_reference=marker, deposit_currency='USD', deposit_amount=100,
                apply_fifo=0, detail_file=f'/private/files/{marker}.csv', detail_source_file=f'/private/files/{marker}.csv',
                detail_hash=marker, detail_rows=[dict(source_row=2, client=wrong.name, client_number=wrong.client_number,
                    client_name=wrong.client_name, loan_number='10002-1', deducted_usd=100)])).insert()
            deposit.append('targets', dict(historical_application=source.rows[0].name, amount_usd=100,
                                           detail_row=deposit.detail_rows[0].name))
            deposit.save(); deposit.submit()
            reconcile_deposit(deposit)
            deposit.reload(); source.reload()
            assert deposit.allocated_usd == 0 and deposit.unclassified_usd == 100
            assert not json.loads(deposit.allocation_detail)
            assert deposit.detail_rows[0].match_status == 'Revisar'
            assert source.rows[0].historical_remitted_usd == 0
            # Correct identity and explicitly reconcile: the valid instruction works.
            right = clients[0]
            deposit.detail_rows[0].update(dict(client=right.name, client_number=right.client_number,
                client_name=right.client_name, loan_number='10001-1'))
            deposit.save()
            reconcile_deposit(deposit)
            deposit.reload(); source.reload()
            assert deposit.allocated_usd == 100 and source.rows[0].historical_remitted_usd == 100
            # Simulate pre-existing inconsistent evidence, only inside this rollback fixture.
            ledger = [dict(tipo='Aplicacion historica', periodo=period.name, aplicacion_id=row.name,
                importe_usd=amount, origen='Selección manual') for row, amount in zip(source.rows, (120, 80))]
            frappe.db.set_value(deposit.doctype, deposit.name, dict(amount_usd=200, allocated_usd=200,
                unallocated_usd=0, unclassified_usd=0, allocation_detail=json.dumps(ledger)), update_modified=False)
            engine._reconcile_sources(company.name)
            source.reload(); period.reload()
            assert source.rows[0].deposit_match_status == 'Diferencia de importe'
            assert source.rows[1].deposit_match_status == 'Depósito parcial'
            assert period.status == 'Parcial' and period.applied_usd == period.remitted_usd == 200
            try:
                close_period(period.name)
            except frappe.ValidationError:
                assert period.reload().status != 'Cerrado'
            else:
                raise AssertionError('A cross-application excess/shortfall closed the period')
            _identical_reload(marker + '-RELOAD')
            return dict(invalid_manual_link_spends_no_cash=True, corrected_identity_can_reconcile=True,
                        identical_paid_reload_preserves_evidence=True,
                        excess_does_not_settle_another_application=True, invalid_period_cannot_close=True, rolled_back=True)
    finally:
        frappe.db.rollback()


def _identical_reload(marker):
    company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
    period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
        payroll_month='2025-04-01', reconciliation_mode='Historica')).insert()
    imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
        source_file=f'/private/files/{marker}.csv', status='Borrador', currency='USD',
        historical_backfill=1, historical_period=period.name)).insert()
    record = dict(source_key=marker, source_row=2, accounting_source_key='', event_type='Aplicacion',
        event_date='2025-04-30', currency='USD', amount=100, amount_usd=100,
        equivalent_amount=100, equivalent_currency='USD', fx_basis='Moneda original USD',
        source_date='2025-04-30', source_account='160209013004', source_currency='USD',
        source_debit=100, source_credit=0, accounting_classification='Aplicación',
        client_number=marker, loan_number='33333-1', client_name=marker)
    with patch.object(engine, '_attached_file', return_value=(frappe._dict(file_name='source.csv'), b'fixture content')), \
         patch.object(engine, 'rename_accounting_import'), \
         patch.object(engine, 'parse_accounting_movements', side_effect=lambda *_: [deepcopy(record)]):
        engine._import_source_file(imported.name)
        imported.reload()
        application = imported.rows[0].name
        deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
            deposit_date='2025-04-30', deposit_reference=marker, deposit_currency='USD', deposit_amount=40,
            targets=[dict(historical_application=application, amount_usd=40)])).insert()
        deposit.submit()
        reconcile_deposit(deposit)
        imported.reload(); deposit.reload()
        before = imported.rows[0].historical_detail
        cash = deposit.allocation_detail
        engine._import_source_file(imported.name)
        imported.reload(); deposit.reload()
        assert imported.rows[0].name == application
        assert imported.rows[0].historical_detail == before
        assert imported.rows[0].historical_remitted_usd == 40
        assert imported.rows[0].historical_balance_usd == 60
        assert deposit.allocation_detail == cash
