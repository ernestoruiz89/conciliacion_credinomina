"""Create/load a period through Quick Entry on the disposable site; roll back."""
from unittest.mock import patch

import frappe
from frappe.core.doctype.file.file import File

from credinomina_reconciliation import period_quick_entry as api
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'QUICK-PERIOD-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker,
                employer_code=marker, payroll_frequency='Quincenal')).insert()
            client = frappe.get_doc(dict(doctype='CN Client', employer=company.name,
                client_name=marker, client_number=marker)).insert()
            old = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2025-07-01', reconciliation_mode='Historica')).insert()
            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                historical_period=old.name, currency='USD', status='Importado',
                source_file=f'/private/files/{marker}-core.csv', rows=[dict(event_type='Aplicacion',
                    event_date='2025-07-15', currency='USD', amount=50, amount_usd=50, effective=1,
                    historical_period=old.name, source_key=marker, client=client.name,
                    client_name=marker, client_number=marker, loan_number='9918565-1')])).insert()
            engine._reconcile_sources(company.name, preserve_deposits=True)
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2025-07-15', deposit_reference=marker, deposit_currency='USD', deposit_amount=50,
                targets=[dict(historical_application=source.rows[0].name, amount_usd=50)])).insert().submit()
            reconcile_deposit(deposit)
            protected = {doc.name: document_state(doc.reload()) for doc in (source, old, deposit)}
            # Emulate the unattached file produced by an Attach control in a dialog.
            # The content is mocked; no test file is left on disk after rollback.
            file = frappe.get_doc(dict(doctype='File', name=marker, file_name=marker + '.csv',
                file_url=f'/private/files/{marker}.csv', is_private=1, owner='Administrator'))
            file.db_insert()
            content = ('Nro. Cliente,Nombre y Apellidos del Cliente,Nro. Crédito,Monto de la cuota en US$\n'
                       f'{marker},{marker},9918565-1,80\n').encode()
            current = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                currency='USD', status='Importado', source_file=f'/private/files/{marker}-current.csv',
                rows=[dict(event_type='Aplicacion', event_date='2026-09-30', currency='USD',
                    amount=80, amount_usd=80, effective=1, source_key=marker+'-current',
                    client=client.name, client_name=marker, client_number=marker, loan_number='9918565-1')])).insert()
            values = dict(employer=company.name, payroll_month='2026-09-01', reconciliation_mode='Operativa',
                          collection_cycle='Segunda quincena', collection_file=file.file_url)
            with patch.object(File, 'get_content', return_value=content):
                result = api.create(values, {'name':current.name, 'modified':str(current.modified)})
            period = frappe.get_doc('CN Reconciliation Period', result['doc']['name'])
            assert result['collection_loaded'] and result['reconciliation']['reviewed'] == 1, result
            assert result['reconciliation']['conforming'] == 1, result
            assert period.expected_usd == period.applied_usd == 80 and period.remitted_usd == 0
            assert len(period.collection_rows) == 1 and period.collection_rows[0].client == client.name
            assert current.reload().historical_period == period.name
            assert current.rows[0].collection_row_id == period.collection_rows[0].name
            assert file.reload().attached_to_name == period.name
            for doc in (source, old, deposit):
                assert document_state(doc.reload()) == protected[doc.name], doc.name
            pending = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                currency='USD', status='Importado', source_file=f'/private/files/{marker}-pending.csv',
                rows=[dict(event_type='Aplicacion', event_date='2026-10-30', currency='USD',
                    amount=80, amount_usd=80, effective=1, source_key=marker+'-pending',
                    client=client.name, client_name=marker, client_number=marker, loan_number='9918565-1')])).insert()
            pending_state = document_state(pending)
            count = frappe.db.count('CN Reconciliation Period', {'employer':company.name})
            frappe.db.savepoint('invalid_quick_period')
            invalid_file = frappe.get_doc(dict(doctype='File', name=marker+'-invalid', file_name=marker+'-invalid.csv',
                file_url=f'/private/files/{marker}-invalid.csv', is_private=1, owner='Administrator'))
            invalid_file.db_insert()
            try:
                with patch.object(File, 'get_content', return_value=b'invalid payroll'):
                    api.create(dict(values, payroll_month='2026-10-01', collection_file=invalid_file.file_url),
                               {'name':pending.name, 'modified':str(pending.modified)})
            except frappe.ValidationError:
                # The HTTP request rolls back the complete POST on failure.
                frappe.db.rollback(save_point='invalid_quick_period')
            else:
                raise AssertionError('Invalid payroll accepted')
            assert frappe.db.count('CN Reconciliation Period', {'employer':company.name}) == count
            assert document_state(pending.reload()) == pending_state
            return dict(payroll_loaded=True, source_link_saved=True, first_reconciliation=True, file_attached=True,
                existing_deposits_and_applications_unchanged=True, invalid_post_rolled_back=True, rolled_back=True)
    finally:
        frappe.db.rollback()
