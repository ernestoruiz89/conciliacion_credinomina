"""Batch-document partial/settled applications; retain executed cash and closure."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_difference_helper import load, save
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.reconciliation_scope import document_state
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources


def snapshot(document):
    values = document.as_dict()
    for key in ('modified', 'modified_by', '_comments'):
        values.pop(key, None)
    for row in values.get('rows', []):
        for key in ('difference_reason', 'difference_notes'):
            row.pop(key, None)
    return values


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'ACCOUNT-DIFF-' + frappe.generate_hash(length=8)
            reason = frappe.get_doc(dict(doctype='CN Difference Reason', reason_name=marker)).insert()
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=company.name,
                payroll_month='2025-07-01', reconciliation_mode='Historica')).insert()
            imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name,
                source_file=f'/private/files/{marker}.csv', currency='USD', status='Importado',
                historical_period=period.name, rows=[dict(source_key=marker + str(i), event_type='Aplicacion',
                    event_date='2025-07-15', effective=1, currency='USD', amount=amount, amount_usd=amount,
                    historical_period=period.name) for i, amount in enumerate((100, 50))])).insert()
            _reconcile_sources(company.name)
            imported.reload()
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_reference=marker, deposit_date='2025-07-15', deposit_currency='USD', deposit_amount=120,
                targets=[dict(historical_application=row.name, amount_usd=amount)
                         for row, amount in zip(imported.rows, (70, 50))])).insert()
            deposit.submit()
            reconcile_deposit(deposit)
            imported.reload(); deposit.reload()
            assert imported.rows[0].historical_balance_usd == 30
            assert imported.rows[1].historical_balance_usd == 0
            frappe.db.set_value(period.doctype, period.name, 'status', 'Cerrado', update_modified=False)
            period.reload()
            baseline = snapshot(imported)
            protected = {doc.name: document_state(doc) for doc in (period, deposit)}
            data = load(imported.name)
            assert len(data['rows']) == 2
            changes = [dict(row_name=row.name, difference_reason=reason.name, difference_notes='Soporte ' + str(i))
                       for i, row in enumerate(imported.rows)]
            result = save(imported.name, data['modified'], changes)
            assert result['updated'] == 2
            imported.reload()
            assert snapshot(imported) == baseline
            assert [row.difference_notes for row in imported.rows] == ['Soporte 0', 'Soporte 1']
            assert len(frappe.get_all('Comment', filters={'reference_doctype': imported.doctype,
                'reference_name': imported.name, 'content': ['like', '%Diferencia documentada%']})) == 2
            repeat = save(imported.name, result['modified'], changes)
            assert repeat['updated'] == 0 and repeat['modified'] == result['modified']
            for doc in (period, deposit):
                assert document_state(doc.reload()) == protected[doc.name], doc.name
            for version, rows in [(data['modified'], changes), (result['modified'],
                    [changes[0], dict(changes[1], row_name='OTHER')])]:
                before = document_state(imported.reload())
                try:
                    save(imported.name, version, rows)
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError('Stale or foreign-row batch was accepted')
                assert document_state(imported.reload()) == before
            return dict(batch_classification=True, audit_comments=True, no_op_idempotent=True,
                partial_and_settled_rows_supported=True, money_and_links_preserved=True,
                closed_period_and_deposit_unchanged=True, stale_and_invalid_batches_rejected=True, rolled_back=True)
    finally:
        frappe.db.rollback()
