"""A future bank receipt never supplies a backdated credit use; rollback only."""
import json
from uuid import uuid4
from unittest.mock import patch

import frappe

from credinomina_reconciliation import client_credit, credit_application, complementary_compensation as offsets
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para el sitio aislado de pruebas')
    frappe.set_user('Administrator')
    doctypes = ('CN Employer', 'CN Client', 'CN Reconciliation Period', 'CN Accounting Import',
        'CN Source Row', 'CN Remittance Allocation', 'CN Complementary Item')
    counts = {doctype: frappe.db.count(doctype) for doctype in doctypes}
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'CREDIT-ORIGIN-' + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            client = frappe.get_doc(dict(doctype='CN Client', employer=employer.name,
                client_number=marker, client_name=marker)).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2025-04-01', reconciliation_mode='Historica')).insert()
            imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name, status='Importado',
                currency='USD', source_file=f'/private/files/{marker}.csv', rows=[dict(event_type='Aplicacion',
                    event_date='2025-04-30', currency='USD', amount=10, amount_usd=10, effective=1,
                    source_key=marker, historical_period=period.name, processing_route='Historica',
                    client=client.name, client_number=marker, loan_number='99522-1')])).insert()
            engine._reconcile_sources(employer.name); imported.reload()
            bank = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_date='2025-05-15', deposit_reference=marker, deposit_currency='USD', deposit_amount=10)).insert()
            bank.submit()

            def credit(date, label, category=client_credit.CATEGORY):
                key = marker + label
                values = dict(doctype='CN Complementary Item', employer=employer.name, category=category,
                    posting_date=date, currency='USD', amount=10, registered_deposit=bank.name,
                    credit_assigned_to='Administrator', credit_commitment_date='2026-11-30',
                    credit_treatment='Aplicación futura', reason_type='Error de la empresa',
                    description='Crédito con fecha de origen verificable', accounting_source_key=key,
                    source_account='160209013004', source_currency='USD', source_debit=0, source_credit=10,
                    source_date=date, source_file=f'/private/files/{marker}.csv', source_row=3,
                    source_file_hash=marker, source_voucher=key, voucher=key, voucher_line=key,
                    accounting_classification='Movimiento interno')
                if category == client_credit.CATEGORY:
                    values['credit_client'] = client.name
                with imported_item(key):
                    return frappe.get_doc(values).insert()

            for index, category in enumerate(client_credit.CREDIT_CATEGORIES):
                savepoint = 'reject_early_credit_' + str(index)
                frappe.db.savepoint(savepoint)
                try:
                    credit('2025-04-01', 'EARLY' + str(index), category)
                except frappe.ValidationError as error:
                    assert 'anterior al depósito' in str(error), str(error)
                    frappe.db.rollback(save_point=savepoint)
                else:
                    raise AssertionError('Se creó un saldo anterior al depósito')
            item = credit('2025-05-15', 'VALID')
            item.submit(); item.reload(); bank.reload()
            # Simulate pre-existing credit evidence with the old wrong date.
            # New mutations must be safe without rewriting that original field.
            frappe.db.set_value(item.doctype, item.name, 'posting_date', '2025-04-01', update_modified=False)
            item.reload()
            key = marker + '-DEBIT'
            with imported_item(key):
                core = frappe.get_doc(dict(doctype='CN Complementary Item', employer=employer.name,
                    category='Por clasificar', posting_date='2025-04-30', currency='USD', amount=10,
                    description='Débito original para compensación', accounting_source_key=key,
                    source_account='160209013004', source_currency='USD', source_debit=10, source_credit=0,
                    source_date='2025-04-30', source_file=f'/private/files/{marker}.csv', source_row=4,
                    source_file_hash=marker, source_voucher=key, voucher=key, voucher_line=key,
                    accounting_classification='Movimiento interno', client_number=marker)).insert()
            before = [doc.reload().as_dict() for doc in (item, core, imported, period, bank)]
            operations = (
                lambda: credit_application.apply(item.name, str(item.modified), imported.rows[0].name, 10, '2025-04-30'),
                lambda: client_credit.record_management(item.name, str(item.modified), 'Devolución', 10, '2025-05-14', marker),
                lambda: offsets.confirm_compensation(item.name, core.name, 10, '2025-05-14', 'Fecha anterior al banco', uuid4().hex),
            )
            for index, operation in enumerate(operations):
                savepoint = 'reject_early_use_' + str(index)
                frappe.db.savepoint(savepoint)
                try:
                    operation()
                except frappe.ValidationError:
                    frappe.db.rollback(save_point=savepoint)
                else:
                    raise AssertionError('Se utilizó crédito antes de recibir el banco')
            assert [doc.reload().as_dict() for doc in (item, core, imported, period, bank)] == before
            operation = uuid4().hex
            offsets.confirm_compensation(item.name, core.name, 10, '2025-05-15', 'Desde el depósito', operation)
            item.reload(); core.reload()
            assert item.credit_pending_usd == 0 and core.compensation_pending_usd == 0
            offsets.reverse_compensation(item.name, operation, '2025-05-15', 'Restaurar capacidad', uuid4().hex)
            item.reload(); core.reload()
            assert item.credit_pending_usd == 10 and core.compensation_pending_usd == 10
            credit_application.apply(item.name, str(item.modified), imported.rows[0].name, 10, '2025-05-15')
            item.reload(); imported.reload(); bank.reload()
            assert item.posting_date.isoformat() == '2025-04-01'
            assert item.credit_pending_usd == 0 and imported.rows[0].historical_remitted_usd == 10
            assert json.loads(item.credit_history)[0]['fecha'] == '2025-05-15'
            assert bank.as_dict() == before[-1]
            return dict(new_client_and_company_credit_respect_bank_date=True, legacy_application_refund_offset_rejected=True,
                bank_date_offset_and_reversal_allowed=True, bank_date_application_allowed=True,
                original_dates_and_cash_unchanged=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert counts == {doctype: frappe.db.count(doctype) for doctype in doctypes}
