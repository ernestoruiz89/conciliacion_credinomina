"""Real database acceptance cases; isolated site only, synthetic data rolled back."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit


def run():
    assert frappe.local.site == 'cn-reconciliation-test.local'
    results = []
    try:
        for requested, applied, cash, pending, surplus in [(100,80,80,0,0), (100,100,80,20,0),
                                                         (100,80,100,0,20), (100,120,120,0,0),
                                                         (0,80,80,0,0)]:
            marker='stages-'+frappe.generate_hash(length=8)
            employer=frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker, payroll_frequency='Mensual')).insert()
            client=frappe.get_doc(dict(doctype='CN Client', employer=employer.name, client_number=marker, client_name=marker)).insert()
            period=frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2026-09-01', reconciliation_mode='Operativa', collection_cycle='Mensual',
                collection_rows=([dict(client=client.name, client_name=client.client_name, row_key=marker,
                    loan_number=marker+'-1', expected_usd=requested)] if requested else []))).insert()
            imported=frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                status='Importado', source_file='/private/files/'+marker+'.csv', rows=[dict(source_row=2,
                    source_key=marker, event_type='Aplicacion', event_date='2026-09-30', historical_period=period.name,
                    client_number=client.client_number, client_name=client.client_name, loan_number=marker+'-1',
                    employer_text=employer.name, currency='USD', amount=applied, amount_usd=applied,
                    processing_route='Operativa', effective=1, match_status='Pendiente')])).insert()
            _reconcile_sources(employer.name, preserve_deposits=True)
            imported.reload(); period.reload()
            deposit=frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_reference=marker, deposit_currency='NIO', fx_rate=36.6243,
                deposit_amount=cash*36.6243, deposit_date='2026-10-10', detail_periods=[dict(period=period.name)])).insert()
            deposit.submit()  # Money is received before the per-client file.
            deposit.append('detail_rows',dict(client_name=client.client_name,client=client.name,
                client_number=client.client_number,loan_number=marker+'-1',deducted_usd=cash))
            deposit.append('targets',dict(historical_application=imported.rows[0].name,amount_usd=min(cash,applied),
                detail_row=deposit.detail_rows[0].name))
            deposit.save()
            reconcile_deposit(deposit)
            period.reload(); deposit.reload()
            assert (period.applied_total_usd,period.pending_usd)==(applied,pending), period.as_dict()
            assert deposit.unclassified_usd==surplus, deposit.as_dict()
            if requested:
                assert period.collection_rows[0].quality_difference_usd==applied-requested
                assert not period.collection_rows[0].deducted_usd
            if not pending and not surplus:
                close_period(period.name)
                period.reload()
                assert period.status=='Cerrado', period.as_dict()
            results.append(dict(requested=requested,applied=applied,cash=cash,pending=period.pending_usd,
                                surplus=deposit.unclassified_usd,status=period.status))
        return results
    finally:
        frappe.db.rollback()
