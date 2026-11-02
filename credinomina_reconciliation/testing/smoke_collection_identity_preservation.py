"""SQL reconciliation preserves the identity behind an existing payroll row ID."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as api


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'COLLECTION-GUARD-' + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            clients = [frappe.get_doc(dict(doctype='CN Client', employer=employer.name,
                client_number=f'{marker}-{i}', client_name=f'{marker} Persona {i}')).insert() for i in range(2)]
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2026-09-01', reconciliation_mode='Operativa', collection_cycle='Mensual',
                collection_rows=[dict(row_key=marker, client=clients[0].name, client_name=clients[0].client_name,
                    loan_number='109-1', installment_number='2', expected_usd=100)])).insert()
            imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                historical_period=period.name, source_file=f'/private/files/{marker}.csv', status='Importado', currency='USD',
                rows=[dict(source_key=marker, event_type='Aplicacion', event_date='2026-09-30', currency='USD',
                    amount=100, amount_usd=100, effective=1, client=clients[0].name, client_number=clients[0].client_number,
                    loan_number='109-1', installment_number='2', historical_period=period.name, processing_route='Operativa')])).insert()
            engine._reconcile_sources(employer.name)
            imported.reload(); period.reload()
            assert json.loads(imported.rows[0].application_allocation_detail)[0]['collection_row_id'] == period.collection_rows[0].name
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_date='2026-09-30', deposit_reference=marker, deposit_currency='USD', deposit_amount=100,
                targets=[dict(historical_application=imported.rows[0].name, amount_usd=100)])).insert()
            deposit.submit()
            reconcile_deposit(deposit)
            imported.reload(); period.reload(); deposit.reload()
            original_period = period.as_dict()
            original_source = imported.as_dict()
            original_cash = deposit.as_dict()

            def rejected(operation):
                frappe.db.savepoint('collection_identity_rejected')
                try:
                    operation()
                except frappe.ValidationError as error:
                    assert 'vinculad' in str(error) or 'duplicada' in str(error), str(error)
                    frappe.db.rollback(save_point='collection_identity_rejected')
                else:
                    raise AssertionError('An existing collection ID changed its financial destination')
                assert period.reload().as_dict() == original_period
                assert imported.reload().as_dict() == original_source
                assert deposit.reload().as_dict() == original_cash

            for field, value in (('client', clients[1].name), ('loan_number', '999-1'),
                                 ('installment_number', '3'), ('row_key', marker + '-OTHER')):
                def change(field=field, value=value):
                    period.collection_rows[0].set(field, value)
                    period.save()
                rejected(change)

            def duplicate():
                period.append('collection_rows', dict(row_key=marker, client=clients[1].name,
                    client_name=clients[1].client_name, loan_number='999-1', expected_usd=100))
                period.save()
            rejected(duplicate)

            # Check real link queries too, rather than relying on cached row totals.
            frappe.db.savepoint('stale_collection_amounts')
            frappe.db.set_value('CN Collection Row', period.collection_rows[0].name,
                dict(applied_usd=0, remitted_usd=0), update_modified=False)
            original_period = period.reload().as_dict()
            def stale_change():
                period.collection_rows[0].client = clients[1].name
                period.save()
            rejected(stale_change)
            frappe.db.rollback(save_point='stale_collection_amounts')
            original_period = period.reload().as_dict()

            content = ('Fila ID,Nro Cliente,Cliente,Credito,Nro Cuota,Cuota USD\n'
                f'{marker},{clients[1].client_number},{clients[1].client_name},999-1,3,100\n').encode()
            with patch.object(api, '_attached_file', return_value=(SimpleNamespace(file_name='corregida.csv'), content)), \
                 patch.object(api, 'enrich_collection_records', return_value=dict(completed_rows=0)):
                rejected(lambda: api.import_collection(period.name))

            # Purely descriptive corrections remain possible and do not rematch cash.
            period.collection_rows[0].client_name = clients[0].client_name + ' corregido'
            period.collection_rows[0].comments = 'Soporte actualizado'
            period.collection_rows[0].loan_number = '000109-1'
            period.save()
            engine._reconcile_sources(employer.name)
            imported.reload(); deposit.reload()
            assert imported.rows[0].application_allocation_detail == original_source['rows'][0]['application_allocation_detail']
            assert imported.rows[0].historical_remitted_usd == 100
            assert deposit.as_dict() == original_cash
            return dict(existing_child_identity_protected=True, explicit_file_row_id_cannot_move_payment=True,
                duplicate_child_cannot_capture_existing_cash=True, stale_cache_uses_real_links=True,
                descriptive_and_credit_format_corrections_allowed=True, cash_and_source_links_preserved=True,
                rolled_back=True)
    finally:
        frappe.db.rollback()
