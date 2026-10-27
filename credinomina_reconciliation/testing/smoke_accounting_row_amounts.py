"""Check row display balances and virtual-field persistence on a disposable site."""
from unittest.mock import patch

import frappe

from credinomina_reconciliation.accounting_client_summary import get_client_summary


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    tables = ['CN Employer', 'CN Accounting Import', 'CN Source Row']
    before = {name: frappe.db.count(name) for name in tables}
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'ROW-AMOUNTS-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name, currency='USD',
                status='Importado', source_file=f'/private/files/{marker}.csv', rows=[dict(event_type='Aplicacion',
                event_date='2026-10-15', currency='USD', amount=100, amount_usd=100, effective=1, source_key=marker,
                client_number=marker, client_name='Cliente de ensayo', loan_number='1-1')])).insert()
            row = imported.rows[0]
            fields = ['amount', 'amount_usd', 'net_applied_usd', 'application_adjustment_usd', 'source_key',
                      'historical_remitted_usd', 'historical_balance_usd', 'historical_detail', 'match_status']
            financial = frappe.db.get_value('CN Source Row', row.name, fields, as_dict=True)
            result = get_client_summary(imported.name)['row_amounts'][row.name]
            assert result['applied_usd'] == 0 and result['balance_usd'] == 100, result
            for field in ('row_applied_usd', 'row_balance_usd'):
                assert frappe.get_meta('CN Source Row').get_field(field).is_virtual
                assert not frappe.db.has_column('CN Source Row', field)
                row.set(field, 999)
            imported.save()
            assert financial == frappe.db.get_value('CN Source Row', row.name, fields, as_dict=True)
            result = get_client_summary(imported.name)['row_amounts'][row.name]
            assert result['applied_usd'] == 0 and result['balance_usd'] == 100, result
            return dict(real_endpoint=True, no_virtual_sql_columns=True,
                        display_values_cannot_change_financial_evidence=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert before == {name: frappe.db.count(name) for name in tables}, 'El ensayo dejó registros'
