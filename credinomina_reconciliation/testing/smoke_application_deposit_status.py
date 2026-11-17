"""Run the naming patch twice; verify every other stored value is unchanged."""
import frappe
from unittest.mock import patch

from credinomina_reconciliation.application_deposit_status import RENAMED
from credinomina_reconciliation.patches.v1_0.simplify_application_deposit_status import execute
from credinomina_reconciliation.reconciliation_scope import document_state


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            marker = 'APP-STATUS-' + frappe.generate_hash(length=8)
            employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker, employer_code=marker)).insert()
            period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                payroll_month='2025-07-01', reconciliation_mode='Historica')).insert()
            statuses = list(RENAMED) + ['Pendiente', 'Conciliada: depósito + ajuste',
                'Diferencia de importe', 'Falta tipo de cambio', 'Ambiguo']
            imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                source_file=f'/private/files/{marker}.csv', currency='USD', status='Importado',
                historical_period=period.name, rows=[dict(source_key=marker + str(i), event_type='Aplicacion',
                    event_date='2025-07-15', effective=1, currency='USD', amount=100, amount_usd=100,
                    historical_period=period.name) for i in range(len(statuses))])).insert()
            deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                deposit_reference=marker, deposit_date='2025-07-15', deposit_currency='USD', deposit_amount=100)).insert()
            deposit.append('targets', dict(historical_application=imported.rows[0].name, amount_usd=100)).db_insert()
            for row, state in zip(imported.rows, statuses):
                frappe.db.set_value(row.doctype, row.name, dict(deposit_match_status=state,
                    difference_reason=None, difference_notes='Conservar documentación manual',
                    historical_remitted_usd=50, historical_balance_usd=50,
                    historical_detail='[{"importe_usd":50,"referencia":"conservar"}]'), update_modified=False)
            # Closed history is renamed without reopening or rebuilding it.
            frappe.db.set_value(period.doctype, period.name, 'status', 'Cerrado', update_modified=False)
            imported.reload(); deposit.reload(); period.reload()
            before = imported.as_dict()
            protected = {doc.name: document_state(doc) for doc in (deposit, period)}
            execute()
            imported.reload()
            after = imported.as_dict()
            for old_row, new_row in zip(before['rows'], after['rows']):
                expected = dict(old_row, deposit_match_status=RENAMED.get(old_row['deposit_match_status'], old_row['deposit_match_status']))
                assert dict(new_row) == expected, old_row['name']
            assert {key: value for key, value in before.items() if key != 'rows'} == {
                key: value for key, value in after.items() if key != 'rows'}
            first = document_state(imported)
            execute()
            assert document_state(imported.reload()) == first, 'Patch is not idempotent'
            for doc in (deposit, period):
                assert document_state(doc.reload()) == protected[doc.name], doc.name
            return dict(renamed=len(RENAMED), remaining_statuses_preserved=True,
                money_links_comments_and_timestamps_preserved=True, closed_period_unchanged=True,
                deposit_and_targets_unchanged=True, idempotent=True, rolled_back=True)
    finally:
        frappe.db.rollback()
