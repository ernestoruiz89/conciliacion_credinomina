"""Idempotent migration over synthetic legacy evidence; no committed fixtures."""
import json

import frappe

from credinomina_reconciliation.patches.v1_0.archive_employer_detail import execute


def run():
    assert frappe.local.site == 'cn-reconciliation-test.local'
    try:
        marker='archive-'+frappe.generate_hash(length=8)
        employer=frappe.get_doc(dict(doctype='CN Employer',employer_name=marker,employer_code=marker)).insert()
        names=[]
        for closed in (False, True):
            period=frappe.get_doc(dict(doctype='CN Reconciliation Period',employer=employer.name,
                payroll_month='2026-09-01',reconciliation_mode='Operativa',collection_cycle='Fecha exacta',
                cutoff_date='2026-09-15' if closed else '2026-09-30')).insert()
            # Emulate pre-upgrade storage without running new-form validation.
            frappe.db.set_value(period.doctype,period.name,dict(application_basis='Detalle de empresa',
                employer_response_file='/private/files/legacy-'+marker+'.xlsx',deducted_usd=100,
                applied_usd=80,remitted_usd=80,applied_total_usd=80,remitted_total_usd=80,pending_usd=0,
                status='Cerrado' if closed else 'Conciliado',historical_fingerprint='preserve-this'),update_modified=False)
            names.append(period.name)
        fields=['name','modified','status','employer_response_file','deducted_usd','applied_usd','remitted_usd',
                'applied_total_usd','remitted_total_usd','pending_usd','historical_fingerprint']
        before=frappe.get_all('CN Reconciliation Period',fields=fields,order_by='name',limit_page_length=0)
        execute()
        first={name:frappe.db.get_value('CN Reconciliation Period',name,'legacy_employer_detail') for name in names}
        execute()
        assert before==frappe.get_all('CN Reconciliation Period',fields=fields,order_by='name',limit_page_length=0)
        for name,evidence in first.items():
            assert evidence==frappe.db.get_value('CN Reconciliation Period',name,'legacy_employer_detail')
            archived=json.loads(evidence)
            assert archived['parent']['deducted_usd']==100
            assert archived['review']
        return dict(periods=len(before),synthetic=len(names),repetitions=2,balances_unchanged=True,files_unchanged=True)
    finally:
        frappe.db.rollback()
