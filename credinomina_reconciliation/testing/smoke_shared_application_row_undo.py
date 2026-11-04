"""Release one of two detail rows paying the same application; rollback only."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.complementary_subcategories import seed_subcategories
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.manual_complementary_policy import imported_item
from credinomina_reconciliation.remittance_row_reconciliation import unreconcile_row
from credinomina_reconciliation.rounding import money
from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import import cn_accounting_import as engine


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para el sitio aislado de pruebas')
    frappe.set_user('Administrator')
    counts = {name: frappe.db.count(name) for name in ('CN Employer', 'CN Client',
        'CN Reconciliation Period', 'CN Accounting Import', 'CN Remittance Allocation', 'CN Complementary Item')}
    try:
        with patch.object(frappe, 'enqueue'), patch.object(frappe, 'publish_realtime'):
            seed_subcategories()
            for signed in (False, True):
                marker = 'SHARED-UNDO-' + frappe.generate_hash(length=8)
                employer = frappe.get_doc(dict(doctype='CN Employer', employer_name=marker,
                    employer_code=marker)).insert()
                client = frappe.get_doc(dict(doctype='CN Client', client_number=marker,
                    client_name=marker, employer=employer.name)).insert()
                period = frappe.get_doc(dict(doctype='CN Reconciliation Period', employer=employer.name,
                    payroll_month='2025-04-01', reconciliation_mode='Historica')).insert()
                imported = frappe.get_doc(dict(doctype='CN Accounting Import', employer=employer.name,
                    source_file=f'/private/files/{marker}.csv', status='Importado', currency='USD', historical_backfill=1,
                    rows=[dict(event_type='Aplicacion', event_date='2025-04-30', currency='USD', amount=150,
                        amount_usd=150, effective=1, historical_period=period.name, source_key=marker,
                        client=client.name, client_number=client.client_number, client_name=client.client_name,
                        loan_number=marker + '-1')])).insert()
                engine._reconcile_sources(employer.name, preserve_deposits=True)
                imported.reload()
                application = imported.rows[0].name
                complement = None
                if signed:
                    with imported_item(marker):
                        complement = frappe.get_doc(dict(doctype='CN Complementary Item', employer=employer.name,
                            category='Ajuste de conciliación', subcategory='Otro ajuste sin CxC',
                            posting_date='2025-05-01', currency='USD', amount=-10, reference=marker,
                            review_action='Partida de depósito', amount_reviewed=1,
                            description='Contrapartida negativa importada', accounting_source_key=marker,
                            source_account='160209013004', source_currency='USD', source_debit=0, source_credit=10,
                            source_date='2025-05-01', source_file=imported.source_file, source_row=20,
                            source_file_hash=marker, source_voucher=marker, voucher=marker, voucher_line=marker,
                            accounting_classification='Movimiento interno')).insert()
                    complement.submit()
                first_amount = 50 if signed else 60
                deposit = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                    deposit_reference=marker, deposit_date='2025-05-01', deposit_currency='USD',
                    deposit_amount=first_amount + 40, detail_file=f'/private/files/{marker}.xlsx',
                    detail_source_file=f'/private/files/{marker}.xlsx', detail_hash=marker,
                    detail_periods=[dict(period=period.name)], detail_rows=[dict(client=client.name,
                        client_number=client.client_number, client_name=client.client_name, employer=employer.name,
                        loan_number=marker + '-1', amount_usd=amount, deducted_usd=amount)
                        for amount in (first_amount, 40)])).insert()
                for row, amount in zip(deposit.detail_rows, (60, 40)):
                    deposit.append('targets', dict(historical_application=application, amount_usd=amount,
                        detail_row=row.name))
                if signed:
                    deposit.append('targets', dict(complementary_item=complement.name, amount_usd=-10,
                        detail_row=deposit.detail_rows[0].name))
                deposit.save(); deposit.submit(); reconcile_deposit(deposit); deposit.reload()
                assert money(deposit.allocated_usd) == first_amount + 40, deposit.as_dict()
                other = frappe.get_doc(dict(doctype='CN Remittance Allocation', employer=employer.name,
                    deposit_reference=marker + '-OTHER', deposit_date='2025-05-02', deposit_currency='USD',
                    deposit_amount=50, targets=[dict(historical_application=application, amount_usd=50)])).insert()
                other.submit(); reconcile_deposit(other); other.reload(); imported.reload()
                assert money(imported.rows[0].historical_remitted_usd) == 150
                assert money(imported.rows[0].historical_balance_usd) == 0
                other_before = other.as_dict()
                retained_row = deposit.detail_rows[1].as_dict()
                retained_entries = [row for row in json.loads(deposit.allocation_detail)
                    if row.get('fila_detalle') == deposit.detail_rows[1].name]
                assert sum(money(row['importe_usd']) for row in retained_entries) == 40, deposit.allocation_detail
                result = unreconcile_row(deposit.name, deposit.detail_rows[0].name, str(deposit.modified))
                deposit.reload(); other.reload(); imported.reload(); period.reload()
                assert result['released_usd'] == first_amount, result
                assert json.loads(deposit.allocation_detail) == retained_entries
                assert money(deposit.allocated_usd) == 40
                assert money(deposit.unallocated_usd) == first_amount
                for field in ('matched_targets', 'linked_usd', 'pending_usd', 'match_status'):
                    assert deposit.detail_rows[1].get(field) == retained_row[field], field
                assert other.as_dict() == other_before, 'Cambió el otro depósito'
                assert money(imported.rows[0].historical_remitted_usd) == 90
                assert money(imported.rows[0].historical_balance_usd) == 60
                assert money(period.pending_usd) == 60
                if signed:
                    from credinomina_reconciliation.complementary_state import flush
                    flush(); complement.reload()
                    assert complement.docstatus == 1 and money(complement.pending_usd) == 10
                reconcile_deposit(deposit); deposit.reload(); imported.reload()
                assert money(deposit.allocated_usd) == 40
                assert money(imported.rows[0].historical_remitted_usd) == 90
                assert deposit.detail_rows[0].match_status == 'Revisar: Pendiente manual'
            return dict(shared_application_rows=True, signed_counterpart_released_together=True,
                other_row_and_deposit_unchanged=True, no_automatic_reselection=True, rolled_back=True)
    finally:
        frappe.db.rollback()
        assert counts == {name: frappe.db.count(name) for name in counts}, 'El ensayo dejó registros'
