"""Create and load periods through both form entry points on a disposable site."""
import json
from unittest.mock import patch

import frappe
from frappe.core.doctype.file.file import File
from frappe.desk.form.save import savedocs

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
            local_name = 'new-cn-reconciliation-period-' + frappe.generate_hash(length=10)
            # Emulate a full-form upload before the document gets its final name.
            file = frappe.get_doc(dict(doctype='File', name=marker, file_name=marker + '.csv',
                file_url=f'/private/files/{marker}.csv', is_private=1, owner='Administrator',
                attached_to_doctype='CN Reconciliation Period', attached_to_name=local_name,
                attached_to_field='collection_file', creation=frappe.utils.now_datetime()))
            file.db_insert()
            content = ('Nro. Cliente,Nombre y Apellidos del Cliente,Nro. Crédito,Monto de la cuota en US$\n'
                       f'{marker},{marker},9918565-1,80\n').encode()

            def core_file(date, amount, label):
                return ('CUENTA_CONTABLE,DESCRIPCION_CTA_CONTABLE,FECHA_APLICA,NO_CMPTE,NO_REF,DESCRIPCION,'
                        'DEBITO_DEL_MES,CREDITO_DEL_MES,NO_CREDITO,EMPRESA,NOMBRE_CLIENTE,TMOV,TDOC\n'
                        f'160209013004,Creditos M.E.,{date},{marker}-{label},{marker}-{label},'
                        f'NOTA AL PRESTAMO 9918565-1 PAGO APLICADO,{amount},0,9918565-1,{company.name},{marker},12,05\n').encode()

            current = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                currency='USD', source_file=f'/private/files/{marker}-current.csv')).insert()
            values = dict(employer=company.name, payroll_month='2026-09-01', reconciliation_mode='Operativa',
                          collection_cycle='Segunda quincena', collection_file=file.file_url)
            draft = dict(values, doctype='CN Reconciliation Period', name=local_name, __islocal=1,
                         __cn_accounting_source={'name':current.name, 'modified':str(current.modified)})
            frappe.response['docs'] = []
            with patch.object(File, 'get_content', return_value=content), \
                 patch.object(engine, '_attached_file', return_value=(frappe._dict(file_name='core.csv'),
                    core_file('2026-09-30', 80, 'current'))), \
                 patch.object(engine, '_reconcile_sources', wraps=engine._reconcile_sources) as reconciled:
                savedocs(json.dumps(draft), 'Save')
                assert reconciled.call_count == 1, 'Payroll and core must share one reconciliation pass'
            saved = next(doc for doc in frappe.response['docs'] if doc['doctype'] == draft['doctype'])
            assert saved['localname'] == local_name and '__cn_accounting_source' not in saved
            period = frappe.get_doc('CN Reconciliation Period', saved['name'])
            current.name = frappe.db.get_value('CN Accounting Import', {'historical_period': period.name}, 'name')
            assert period.expected_usd == period.applied_usd == 80 and period.remitted_usd == 0
            assert len(period.collection_rows) == 1 and period.collection_rows[0].client == client.name
            assert current.reload().historical_period == period.name
            assert current.rows[0].collection_row_id == period.collection_rows[0].name
            assert file.reload().attached_to_name == period.name
            assert current.status in ('Importado', 'Importado con excepciones')

            # No payroll is required for either historical or operative creation.
            for mode, month in (('Historica', '2025-08'), ('Operativa', '2026-10')):
                imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                    currency='USD', source_file=f'/private/files/{marker}-{mode}.csv')).insert()
                with patch.object(engine, '_attached_file', return_value=(frappe._dict(file_name='core.csv'),
                        core_file(month+'-15', 35, mode))):
                    fields = dict(employer=company.name, payroll_month=month+'-01',
                                  reconciliation_mode=mode, collection_cycle='Primera quincena')
                    link = {'name':imported.name, 'modified':str(imported.modified)}
                    if mode == 'Historica':
                        historical_draft = dict(fields, doctype=draft['doctype'], name=local_name, __islocal=1,
                            historical_scope='Fecha exacta', historical_application_date=month+'-15',
                            __cn_accounting_source=link)
                        frappe.response['docs'] = []
                        savedocs(json.dumps(historical_draft), 'Save')
                        created_doc = next(doc for doc in frappe.response['docs'] if doc['doctype'] == draft['doctype'])
                        assert str(created_doc['historical_application_date']) == month+'-15'
                    else:
                        created = api.create(fields, link)
                        assert created['movements_loaded'] and not created['collection_loaded']
                        created_doc = created['doc']
                new_period = frappe.get_doc('CN Reconciliation Period', created_doc['name'])
                imported.name = frappe.db.get_value('CN Accounting Import', {'historical_period':new_period.name}, 'name')
                assert imported.reload().historical_period == new_period.name
                assert len(imported.rows) == 1 and imported.rows[0].historical_period == new_period.name
                assert new_period.applied_usd == 35 and new_period.remitted_usd == 0
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

            frappe.db.savepoint('invalid_quick_core')
            try:
                with patch.object(engine, '_attached_file', return_value=(frappe._dict(file_name='core.csv'), b'invalid core')):
                    api.create(dict(employer=company.name, payroll_month='2026-10-01',
                        reconciliation_mode='Operativa', collection_cycle='Segunda quincena'),
                        {'name':pending.name, 'modified':str(pending.modified)})
            except frappe.ValidationError:
                frappe.db.rollback(save_point='invalid_quick_core')
            else:
                raise AssertionError('Invalid accounting file accepted')
            assert frappe.db.count('CN Reconciliation Period', {'employer':company.name}) == count
            assert document_state(pending.reload()) == pending_state
            return dict(payroll_loaded=True, source_link_saved=True, first_reconciliation=True, file_attached=True,
                historical_and_operative_core_loaded=True, single_reconciliation_pass=True,
                existing_deposits_and_applications_unchanged=True, invalid_post_rolled_back=True, rolled_back=True)
    finally:
        frappe.db.rollback()
