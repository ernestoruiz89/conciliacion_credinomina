"""Rollback-only SQL verification of credit funding across closed origin periods."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation import client_credit, credit_application
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.testing.smoke_client_credit import must_fail
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'CREDIT-APPLY-' + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype='CN Client', employer=employer.name, client_name=marker, client_number=marker)).insert()

            def application(month, date, amount, mode='Historica'):
                period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                    payroll_month=month, reconciliation_mode=mode, collection_cycle='Mensual',
                    historical_scope='Fecha exacta', historical_application_date=date))
                if mode == 'Operativa':
                    period.append('collection_rows', dict(row_key=marker + date, client=client.name,
                        client_name=client.client_name, client_number=marker, loan_number='0011125-1',
                        expected_usd=amount))
                period.insert()
                imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                    source_file='/private/files/' + marker + date + '.csv', currency='USD', status='Importado'))
                imported.append('rows', dict(event_type='Aplicacion', event_date=date, source_key=marker + date,
                    client=client.name, client_name=client.client_name, client_number=marker, loan_number='0011125-1',
                    amount=amount, amount_usd=amount, currency='USD', effective=1, historical_period=period.name,
                    processing_route=mode, voucher=marker + date))
                imported.insert()
                _reconcile_sources(employer.name)
                imported.reload()
                return period, imported

            origin_period, origin_import = application('2026-04-01', '2026-04-30', 100)
            detail_file = '/private/files/' + marker + '-detail.csv'
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_reference=marker, deposit_date='2026-04-30', deposit_currency='USD', deposit_amount=120.77,
                detail_file=detail_file, detail_source_file=detail_file, detail_hash=marker,
                detail_periods=[dict(period=origin_period.name)]))
            deposit.append('detail_rows', dict(client=client.name, client_number=marker, client_name=client.client_name,
                loan_number='0011125-1', deducted_usd=120.77, source_row=2))
            deposit.insert(); deposit.submit()
            reconcile_deposit(deposit)
            deposit.reload()
            item = frappe.get_doc(dict(doctype='CN Complementary Item', category=client_credit.CATEGORY,
                posting_date='2026-04-30', currency='USD', amount=20.77, registered_deposit=deposit.name,
                employer=employer.name, credit_client=client.name, credit_detail_row=deposit.detail_rows[0].name,
                credit_assigned_to='Administrator', credit_commitment_date='2026-06-30', credit_treatment='Aplicación futura',
                reason_type='Otro por aclarar', description='Saldo disponible')).insert()
            item.submit(); item.reload()
            assert item.result == client_credit.RESULT
            close_period(origin_period.name)
            deposit.reload(); origin_period.reload()
            frozen_deposit = deposit.as_dict()
            frozen_period = origin_period.as_dict()
            target_period, target_import = application('2026-05-01', '2026-05-30', 20.76)
            target = target_import.rows[0]
            options = credit_application.candidates(item.name)
            assert [row['name'] for row in options['rows']] == [target.name], options
            stale = str(item.modified)
            args = dict(item_name=item.name, modified=stale, application_id=target.name,
                        amount_usd=20.76, event_date='2026-05-30')
            for value in (0, -1, 20.77):
                must_fail(lambda value=value: credit_application.apply(**dict(args, amount_usd=value)))
            result = credit_application.apply(**args)
            assert result['pending_usd'] == .01 and result['application_pending_usd'] == 0, result
            item.reload(); target_import.reload(); target_period.reload()
            assert target_period.pending_usd == 0 and target_period.remitted_total_usd == 20.76, target_period.as_dict()
            assert item.credit_resolved_usd == 20.76 and item.credit_pending_usd == .01
            assert json.loads(target_import.rows[0].historical_detail)[0]['saldo_a_favor'] == item.name
            must_fail(lambda: credit_application.apply(**args))
            must_fail(lambda: credit_application.apply(**dict(args, modified=str(item.modified), amount_usd=.01)))
            must_fail(lambda: item.cancel())
            frappe.db.savepoint('closed_credit_destination')
            close_period(target_period.name)
            item.reload()
            operation = json.loads(item.credit_history)[0]['operation_id']
            must_fail(lambda: client_credit.reverse_management(item.name, str(item.modified), operation, '2026-06-01', 'Período cerrado'))
            frappe.db.rollback(save_point='closed_credit_destination')
            # Rebuilding sources must preserve the funding and closed origin.
            _reconcile_sources(employer.name)
            target_import.reload(); target_period.reload(); deposit.reload(); origin_period.reload()
            assert target_import.rows[0].historical_remitted_usd == 20.76
            assert target_period.pending_usd == 0
            assert deposit.as_dict() == frozen_deposit, 'Original deposit changed'
            assert origin_period.as_dict() == frozen_period, 'Closed origin changed'
            item.reload()
            operation = json.loads(item.credit_history)[0]['operation_id']
            result = client_credit.reverse_management(item.name, str(item.modified), operation, '2026-06-01', 'Corrección de vínculo')
            assert result['pending_usd'] == 20.77
            target_period.reload(); target_import.reload(); item.reload()
            assert target_period.pending_usd == 20.76
            assert target_import.rows[0].historical_remitted_usd == 0
            must_fail(lambda: client_credit.reverse_management(item.name, str(item.modified), operation, '2026-06-01', 'Duplicada'))
            operative, op_import = application('2026-09-01', '2026-09-30', 20.76, 'Operativa')
            item.reload()
            result = credit_application.apply(item.name, str(item.modified), op_import.rows[0].name, 20.76, '2026-09-30')
            operative.reload(); op_import.reload(); deposit.reload(); origin_period.reload()
            assert operative.pending_usd == 0 and operative.remitted_total_usd == 20.76, operative.as_dict()
            assert op_import.rows[0].historical_remitted_usd == 20.76
            assert deposit.as_dict() == frozen_deposit and origin_period.as_dict() == frozen_period
            return dict(partial_application=True, remainder_usd=.01, closed_origin_unchanged=True,
                        repeated_reconciliation=True, duplicate_and_overpayment_rejected=True, reversal=True,
                        closed_destination_guard=True, operative_destination=True)
    finally:
        frappe.db.rollback()
