"""Missing core payment stays visible until corrected; synthetic data, rollback only."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period import cn_reconciliation_period as period_api
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import get_work_overview, get_control_rows
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'PENDING-' + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker,
                employer_code=marker, payroll_frequency='Mensual')).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2026-09-01', reconciliation_mode='Operativa',
                collection_cycle='Mensual', application_basis='Cobranza')).insert()
            content = ('Nro. Cliente,Nombre y Apellidos del Cliente,Nro. Crédito,Monto de la cuota en US$\n'
                       f'{marker}-1,Cliente aplicado,12888-1,85.38\n'
                       f'{marker}-2,Cliente pendiente,13997-1,28.43\n').encode('utf-8')
            with patch.object(period_api, '_attached_file', return_value=(
                frappe._dict(file_name='cobranza.csv'), content)):
                period_api.import_collection(period.name)
            period.reload()
            first, missing = period.collection_rows

            def application(row, amount, suffix):
                return dict(event_type='Aplicacion', source_key=marker + suffix,
                    event_date='2026-09-15', currency='USD', amount=amount, amount_usd=amount,
                    client=row.client, client_name=row.client_name, client_number=row.client_number,
                    loan_number=row.loan_number, effective=1, processing_route='Operativa',
                    historical_period=period.name)

            source = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                source_file=f'/private/files/{marker}.csv', currency='USD', status='Importado',
                historical_period=period.name, rows=[application(first, 85.38, '-first')])).insert()
            result = period_api.reconcile_first(period.name)
            assert result['reviewed'] == 2 and result['differences'] == 1, result
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_date='2026-09-12', deposit_reference=marker, deposit_currency='USD',
                deposit_amount=113.81, detail_periods=[dict(period=period.name)],
                detail_file=f'/private/files/{marker}.xlsx', detail_source_file=f'/private/files/{marker}.xlsx',
                detail_hash=marker, detail_rows=[dict(client=row.client, client_name=row.client_name,
                    client_number=row.client_number, loan_number=row.loan_number,
                    deducted_usd=row.expected_usd, source_row=index + 2)
                    for index, row in enumerate(period.collection_rows)])).insert()
            deposit.submit()
            reconcile_deposit(deposit)
            deposit.reload()
            assert deposit.unclassified_usd == 28.43, deposit.as_dict()
            cash_before = deposit.allocation_detail
            overview = get_work_overview(employer.name)
            actions = [action for case in overview['work_items'] for action in case['actions']
                       if action['kind'] == 'pending_application']
            assert len(actions) == 1 and actions[0]['amount_usd'] == 28.43, actions
            assert actions[0]['loan_number'] == '13997-1', actions
            filtered = get_control_rows('work_items', year='Todos', employer=employer.name, work_kind='payments')
            assert filtered['count'] == 1 and filtered['rows'][0]['amount_usd'] == 28.43, filtered
            assert not frappe.db.exists('CN Complementary Item', {'employer': employer.name}), 'Invented a credit'

            source.reload()
            source.append('rows', application(missing, 28.43, '-corrected'))
            source.save()
            result = period_api.reconcile_first(period.name)
            assert result['conforming'] == 2 and result['differences'] == 0, result
            deposit.reload()
            assert deposit.allocation_detail == cash_before, 'First reconciliation changed cash'
            reconcile_deposit(deposit)
            deposit.reload()
            assert deposit.allocated_usd == 113.81 and deposit.unclassified_usd == 0, deposit.as_dict()
            assert get_control_rows('work_items', year='Todos', employer=employer.name, work_kind='payments')['count'] == 0
            return dict(pending_usd=28.43, credit_created=False, corrected_and_reconciled=True, rolled_back=True)
    finally:
        frappe.db.rollback()
