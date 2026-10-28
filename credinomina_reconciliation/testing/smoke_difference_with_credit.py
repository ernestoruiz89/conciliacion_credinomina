"""Actual reconciliation of a documented difference plus a core-backed credit."""
from unittest.mock import patch
import frappe
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.deposit_difference import record, SETTLED
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.reclassification_distribution import link_deposit
from credinomina_reconciliation.complementary_balances import get_balance


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    marker = 'DIFF-CREDIT-' + frappe.generate_hash(length=8)
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            clients = [frappe.get_doc(dict(doctype='CN Client', employer=company.name,
                client_name='Cliente ' + str(i), client_number=marker + str(i))).insert() for i in range(2)]
            loans = [marker + '-1', marker + '-2']
            frappe.get_doc(dict(doctype='CN Credit Portfolio Snapshot', source_file=f'/private/files/{marker}.xlsx',
                report_date='2049-10-31', status='Importado', rows=[dict(credit_number=loans[i],
                    credit_status='CORRIENTE' if i == 0 else 'CANCELADO', employer=company.name,
                    matched_client=client.name, client_number_core=client.client_number, client_name=client.client_name)
                    for i, client in enumerate(clients)])).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2025-10-01', reconciliation_mode='Historica', historical_scope='Fecha exacta',
                historical_application_date='2025-10-31', collection_cycle='Mensual')).insert()
            parent = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name, currency='USD',
                source_file=f'/private/files/{marker}.csv', status='Importado', historical_period=period.name,
                rows=[dict(event_type='Aplicacion', event_date='2025-10-31', source_key=marker,
                    client_name=clients[0].client_name, client_number=clients[0].client_number, client=clients[0].name,
                    loan_number=loans[0], amount=2783.58, amount_usd=2783.58, currency='USD', effective=1,
                    match_status='Conciliado', historical_period=period.name, processing_route='Historica')])).insert()
            file = f'/private/files/{marker}-detail.csv'
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_reference=marker, deposit_date='2025-11-12', deposit_currency='USD', deposit_amount=2808.82,
                detail_file=file, detail_source_file=file, detail_hash=marker, apply_fifo=1,
                detail_periods=[dict(period=period.name)], detail_rows=[dict(client=client.name,
                    client_name=client.client_name, client_number=client.client_number, employer=company.name,
                    loan_number=loans[i], deducted_usd=2783.58 if i == 0 else 25.22, source_row=i+2)
                    for i, client in enumerate(clients)])).insert()
            deposit.submit(); deposit.reload()
            reason = frappe.get_doc(dict(doctype='CN Difference Reason', reason_name=marker)).insert()
            record(deposit.name, str(deposit.modified), reason.name)
            deposit.reload(); reconcile_deposit(deposit); deposit.reload()
            assert deposit.detail_rows[2].difference_notes == ''
            with imported_item(marker):
                item = frappe.get_doc(dict(doctype='CN Complementary Item', employer=company.name,
                    category='Por clasificar', review_action='Pendiente de revisión', client_name=clients[1].client_name,
                    currency='USD', amount=25.22, posting_date='2025-12-31', source_date='2025-12-31',
                    source_currency='USD', source_debit=25.22, source_credit=0, accounting_source_key=marker,
                    voucher=marker, voucher_line=marker, source_voucher=marker, description='Reclasificación del core')).insert()
            frappe.db.set_value(item.doctype, item.name, dict(docstatus=1, category='Compensación entre partidas',
                review_action='Compensación entre partidas'), update_modified=False)
            item.reload()
            link_deposit(item.name, deposit.name, deposit.detail_rows[1].name, str(item.modified), str(deposit.modified),
                         25.22, reason='Saldo a favor identificado')
            for _ in range(2):
                deposit.reload(); reconcile_deposit(deposit); deposit.reload(); parent.reload()
                assert deposit.allocated_usd == 2783.58, deposit.as_dict()
                assert deposit.justified_surplus_usd == 25.22
                assert deposit.unclassified_usd == .02
                assert deposit.detail_status == SETTLED
                assert deposit.detail_rows[0].match_status == deposit.detail_rows[1].match_status == 'Conciliada'
                assert deposit.detail_rows[1].client_credit_usd == 25.22 and deposit.detail_rows[1].pending_usd == 0
                assert deposit.detail_rows[2].difference_core_pending_usd == .02
                assert parent.rows[0].historical_balance_usd == 0
                assert get_balance(item.name)['management_pending_usd'] == 25.22
            # The helper must edit classifications even with a closed period,
            # without saving the financial document or replaying reconciliation.
            from credinomina_reconciliation.deposit_difference_helper import load, save
            frappe.db.set_value(period.doctype, period.name, 'status', 'Cerrado')
            def business_state(doc):
                data = doc.reload().as_dict()
                for field in ('modified', 'modified_by', '_comments'):
                    data.pop(field, None)
                for row in data.get('detail_rows') or []:
                    row.pop('difference_reason', None); row.pop('difference_notes', None)
                return data
            before = business_state(deposit)
            applications = business_state(parent)
            closed = business_state(period)
            snapshot = load(deposit.name)
            assert len(snapshot['rows']) == 2
            with patch.object(type(deposit), 'save', side_effect=AssertionError('No guardar el documento financiero')), \
                    patch('credinomina_reconciliation.deposit_reconciliation.reconcile_deposit',
                          side_effect=AssertionError('No volver a conciliar')):
                changes = [dict(row_name=row['row_name'], difference_reason=reason.name,
                                difference_notes='Clasificación desde el helper') for row in snapshot['rows']]
                saved = save(deposit.name, snapshot['modified'], changes)
                assert saved['updated'] == 2
                edited = save(deposit.name, saved['modified'], [dict(row_name=snapshot['rows'][0]['row_name'],
                    difference_reason=reason.name, difference_notes='')])
                assert edited['updated'] == 1
            assert business_state(deposit) == before
            assert business_state(parent) == applications
            assert business_state(period) == closed
            assert get_balance(item.name)['management_pending_usd'] == 25.22
            return dict(applied=2783.58, client_credit=25.22, difference=.02, repeated_safely=True,
                        helper_saved_without_reconciliation=True, closed_period_preserved=True, rolled_back=True)
    finally:
        frappe.db.rollback()
