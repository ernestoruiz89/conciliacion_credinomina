"""Rollback-only: FIFO keeps completed detail links on different credits."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.allocation_origin import DETAIL, FIFO
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'), \
                patch('frappe.model.rename_doc.sync_user_settings'):
            marker = 'DETAIL-FIFO-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker,
                payroll_frequency='Quincenal')).insert()
            clients = [frappe.get_doc(dict(doctype='CN Client', employer=company.name, client_number=marker + str(index),
                client_name='Cliente prueba ' + str(index))).insert() for index in (1, 2)]
            periods, imports = [], []
            for day, first, second, loan in (('15', 30.21, 20, '123001-1'), ('30', 52.89, 5, '123002-1')):
                period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                    payroll_month='2026-06-01', reconciliation_mode='Historica', historical_scope='Fecha exacta',
                    historical_application_date='2026-06-' + day,
                    collection_cycle='Primera quincena' if day == '15' else 'Segunda quincena')).insert()
                imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                    historical_backfill=1, historical_period=period.name, status='Importado', currency='USD',
                    source_file=f'/private/files/{marker}-{day}.csv', rows=[
                        dict(source_key=marker + day + str(index), event_type='Aplicacion', event_date='2026-06-' + day,
                            currency='USD', amount=amount, amount_usd=amount, effective=1, client=client.name,
                            client_number=client.client_number, client_name=client.client_name, loan_number=credit,
                            processing_route='Historica', historical_period=period.name)
                        for index, client, amount, credit in ((1,clients[0],first,loan), (2,clients[1],second,'123003-1'))])).insert()
                periods.append(period)
                imports.append(imported)
            _reconcile_sources(company.name, preserve_deposits=True)
            fixed = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2026-07-01', deposit_reference=marker + '-FIXED', deposit_currency='USD', deposit_amount=5,
                targets=[dict(historical_application=imports[0].rows[1].name, amount_usd=5)])).insert()
            fixed.submit()
            reconcile_deposit(fixed)
            fixed_before = document_state(fixed.reload())
            document = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2026-07-07', deposit_reference=marker, deposit_currency='USD', deposit_amount=100,
                apply_fifo=0, detail_file=f'/private/files/{marker}.xlsx', detail_source_file=f'/private/files/{marker}.xlsx',
                detail_hash=marker, detail_periods=[dict(period=period.name) for period in periods],
                detail_rows=[dict(source_row=index + 2, employer=company.name, client=client.name,
                    client_number=client.client_number, client_name=client.client_name, loan_number=loan,
                    deducted_usd=amount, amount_usd=amount) for index, client, loan, amount in (
                        (0,clients[0],'',83.10), (1,clients[1],'123003-1',16.90))])).insert()
            document.submit()
            reconcile_deposit(document)
            document.reload()
            original = json.loads(document.allocation_detail)
            assert len(original) == 2 and all(entry['origen'] == DETAIL for entry in original), original
            assert money(document.allocated_usd) == money(83.10), document.as_dict()
            assert document.detail_rows[0].loan_number == '' and document.detail_rows[0].match_status == 'Conciliada'
            old_ids = {entry['aplicacion_id'] for entry in original}

            def paid_sources():
                return [dict((field, imported.reload().rows[0].get(field)) for field in (
                    'name', 'client', 'loan_number', 'amount', 'historical_period', 'historical_remitted_usd', 'historical_detail'))
                    for imported in imports]

            paid_before = paid_sources()
            document.apply_fifo = 1
            document.save()
            before_failure = document_state(document.reload())
            frappe.db.savepoint('detail_fifo_failure')
            try:
                with patch('credinomina_reconciliation.deposit_reconciliation.assert_preserved_allocations',
                           side_effect=RuntimeError('Injected failure after allocation')):
                    reconcile_deposit(document)
                raise AssertionError('Expected injected failure')
            except RuntimeError:
                frappe.db.rollback(save_point='detail_fifo_failure')
            assert document_state(document.reload()) == before_failure
            assert paid_sources() == paid_before and document_state(fixed.reload()) == fixed_before
            for _ in range(2):
                reconcile_deposit(document)
                document.reload()
                ledger = json.loads(document.allocation_detail)
                assert [entry for entry in ledger if entry.get('aplicacion_id') in old_ids] == original, ledger
                assert len(ledger) == 4 and money(document.allocated_usd) == 100, ledger
                assert all(entry['origen'] == FIFO for entry in ledger if entry.get('aplicacion_id') not in old_ids)
                assert not money(document.unallocated_usd) and not document.targets
                assert all(row.match_status == 'Conciliada' for row in document.detail_rows)
                assert 'Asignaciones confirmadas conservadas' in document.detail_rows[0].match_reason
                assert paid_sources() == paid_before and document_state(fixed.reload()) == fixed_before
            return dict(previous_links=2, previous_usd=83.10, newly_allocated_usd=16.90,
                different_credits_preserved=True, other_deposit_unchanged=True,
                repeat_without_duplicates=True, failure_rolled_back=True, rollback=True)
    finally:
        frappe.db.rollback()
