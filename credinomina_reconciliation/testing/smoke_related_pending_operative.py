"""Read-only operative pending balances over real cash/adjustment fixtures."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation import accounting_related_records as related
from credinomina_reconciliation import reconciliation_workbench as workbench
from credinomina_reconciliation.application_adjustments import confirm_adjustment
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.manual_complementary_policy import imported_item
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
            marker = 'RELATED-OP-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype='CN Client', employer=company.name,
                client_number=marker, client_name='Cliente saldo operativo')).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2026-09-01', reconciliation_mode='Operativa', collection_cycle='Mensual', application_basis='Cobranza',
                collection_rows=[dict(row_key=marker, client=client.name, client_name=client.client_name,
                    client_number=client.client_number, loan_number='13528-1', expected_usd=42.40)])).insert()
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                source_file=f'/private/files/{marker}.csv', status='Importado', currency='USD',
                rows=[dict(event_type='Aplicacion', event_date='2026-09-15', source_key=marker,
                    currency='USD', amount=42.40, amount_usd=42.40, effective=1, processing_route='Operativa',
                    client=client.name, client_number=client.client_number, client_name=client.client_name,
                    loan_number='13528-1')])).insert()
            _reconcile_sources(company.name, preserve_deposits=True)
            assert not source.reload().rows[0].historical_period
            documents = [company, client, period, source]

            def check(expected, net=42.40):
                before = [document_state(doc.reload()) for doc in documents]
                result = related.get_related_pending(source.name, source.rows[0].name)
                mesa = workbench.get_data(context=result['context'])
                for data in (result, mesa):
                    row = next(row for row in data['applications'] if row['row_name'] == source.rows[0].name)
                    assert money(row['pending_usd']) == money(expected), row
                    assert money(row['amount_usd']) == money(net), row
                assert before == [document_state(doc.reload()) for doc in documents], 'Consultation changed stored reconciliation'
                return result

            check(42.40)
            # Assign the same operative application using the current direct
            # period workflow; its monetary evidence must remain readable too.
            source.historical_period = period.name
            source.save()
            _reconcile_sources(company.name, preserve_deposits=True)
            assert source.reload().rows[0].collection_period == period.name, source.rows[0].as_dict()
            deposits = []
            for index, amount in enumerate((10,5)):
                deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                    deposit_reference=f'{marker}-{index}', deposit_date='2026-09-30', deposit_currency='USD', deposit_amount=amount,
                    targets=[dict(historical_application=source.rows[0].name, amount_usd=amount)])).insert()
                deposit.submit()
                reconcile_deposit(deposit)
                deposits.append(deposit)
                documents.append(deposit)
            check(27.40)
            key = marker + '-ADJ'
            with imported_item(key):
                adjustment = frappe.get_doc(dict(doctype='CN Complementary Item', employer=company.name,
                    period=period.name, posting_date='2026-09-30', category='Ajuste de aplicación',
                    review_action='Ajuste de aplicación', currency='USD', amount=7.40, application_adjustment_usd=7.40,
                    related_application=source.rows[0].name, related_import=source.name, description='Reducción del core de prueba',
                    accounting_source_key=key, source_currency='USD', source_credit=7.40, source_date='2026-09-30')).insert()
                confirm_adjustment(adjustment.name)
            documents.append(adjustment)
            result = check(20,35)
            row = result['applications'][0]
            assert {link['document'] for link in row['deposits']} == {doc.name for doc in deposits}, row
            assert adjustment.name in {link['document'] for link in row['complementary_items']}, row
            assert source.rows[0].amount == 42.40
            assert all(money(doc.reload().allocated_usd) == money(amount) for doc, amount in zip(deposits,(10,5)))
            return dict(without_deposit_usd=42.40, after_two_deposits_usd=27.40,
                confirmed_adjustment_usd=7.40, pending_usd=20,
                modal_and_workbench_agree=True, stored_reconciliation_unchanged=True, rollback=True)
    finally:
        frappe.db.rollback()
