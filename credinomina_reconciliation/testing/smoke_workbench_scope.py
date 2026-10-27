"""Verify scoped navigation data against real documents; disposable site only."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation import accounting_related_records as related
from credinomina_reconciliation import reconciliation_workbench as workbench


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    tables = ['CN Employer', 'CN Client', 'CN Accounting Import', 'CN Source Row',
              'CN Remittance Allocation', 'CN Remittance Detail']
    before = {name: frappe.db.count(name) for name in tables}
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'WB-SCOPE-' + frappe.generate_hash(length=8)
            company = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            clients = [frappe.get_doc(dict(doctype='CN Client', employer=company.name,
                client_number=marker + str(index), client_name=f'Cliente mesa {index}')).insert() for index in (1, 2)]
            imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=company.name, currency='USD',
                status='Importado', source_file=f'/private/files/{marker}.csv', rows=[dict(event_type='Aplicacion',
                event_date='2026-10-15', currency='USD', amount=10, amount_usd=10, effective=1, source_key=client.name,
                client=client.name, client_number=client.client_number, client_name=client.client_name,
                loan_number=f'{index}-1') for index, client in enumerate(clients, 1)])).insert()
            client = clients[0]
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=company.name,
                deposit_date='2026-10-15', deposit_reference=marker, deposit_currency='USD', deposit_amount=10,
                detail_file=f'/private/files/{marker}.csv', detail_source_file=f'/private/files/{marker}.csv',
                detail_hash=marker, detail_rows=[dict(source_row=1, client_name=client.client_name,
                client_number=client.client_number, client=client.name, loan_number='1-1', deducted_usd=10)])).insert()
            origins = [related.get_related_pending(imported.name, imported.rows[0].name),
                       related.get_related_pending_for_remittance(deposit.name, deposit.detail_rows[0].name)]
            snapshots = {(doc.doctype, doc.name): frappe.db.get_value(doc.doctype, doc.name, 'modified')
                         for doc in [company, *clients, imported, deposit]}
            for origin in origins:
                # Browser URL parameters and direct object navigation must agree.
                for context in (origin['context'], json.dumps(origin['context'])):
                    result = workbench.get_data(employer=origin['employer'], context=context)
                    assert result['employer'] == company.name and result['client'] == client.name, result
                    assert [row['row_name'] for row in result['applications']] == [imported.rows[0].name], result
                    assert imported.rows[1].name not in {row['row_name'] for row in result['applications']}
            manual = workbench.get_data(employer=company.name, client=client.name)
            assert manual['client'] == client.name
            assert all(frappe.db.get_value(doctype, name, 'modified') == modified
                       for (doctype, name), modified in snapshots.items())
            return dict(import_and_deposit_origins=True, object_and_json_context=True,
                        exact_company_and_client=True, excludes_other_client=True, read_only=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert before == {name: frappe.db.count(name) for name in tables}, 'El ensayo dejó registros'
