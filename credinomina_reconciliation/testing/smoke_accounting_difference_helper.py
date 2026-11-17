"""Document pending applications, excluding settled rows and preserving cash."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_difference_helper import load, save
from credinomina_reconciliation.accounting_client_summary import get_client_summary
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
            summary = get_client_summary(imported.name)
            by_source = {row['source_row_name']: row for row in summary['rows']}
            for source, expected in zip(imported.rows, ((100, 70, 0, 30), (50, 50, 0, 0))):
                assert tuple(by_source[source.name][field] for field in
                    ('amount_usd', 'deposits_usd', 'complementary_usd', 'balance_usd')) == expected
            assert snapshot(imported.reload()) == baseline
            data = load(imported.name)
            assert [row['row_name'] for row in data['rows']] == [imported.rows[0].name]
            assert data['rows'][0]['amount_usd'] == 100
            assert data['rows'][0]['balance_usd'] == 30
            changes = [dict(row_name=row.name, difference_reason=reason.name, difference_notes='Soporte ' + str(i))
                       for i, row in enumerate(imported.rows[:1])]
            result = save(imported.name, data['modified'], changes)
            assert result['updated'] == 1
            imported.reload()
            assert snapshot(imported) == baseline
            assert imported.rows[0].difference_notes == 'Soporte 0'
            assert not imported.rows[1].difference_notes
            assert len(frappe.get_all('Comment', filters={'reference_doctype': imported.doctype,
                'reference_name': imported.name, 'content': ['like', '%Diferencia documentada%']})) == 1
            repeat = save(imported.name, result['modified'], changes)
            assert repeat['updated'] == 0 and repeat['modified'] == result['modified']
            for doc in (period, deposit):
                assert document_state(doc.reload()) == protected[doc.name], doc.name
            for version, rows in [(data['modified'], changes), (result['modified'],
                    [changes[0], dict(changes[0], row_name='OTHER')]), (result['modified'],
                    [changes[0], dict(changes[0], row_name=imported.rows[1].name)])]:
                before = document_state(imported.reload())
                try:
                    save(imported.name, version, rows)
                except frappe.ValidationError:
                    pass
                else:
                    raise AssertionError('Stale or foreign-row batch was accepted')
                assert document_state(imported.reload()) == before
            return dict(batch_classification=True, audit_comments=True, no_op_idempotent=True,
                client_summary_components=True, pending_rows_only=True, settled_rows_excluded_and_not_editable_in_batch=True, money_and_links_preserved=True,
                closed_period_and_deposit_unchanged=True, stale_and_invalid_batches_rejected=True, rolled_back=True)
    finally:
        frappe.db.rollback()
