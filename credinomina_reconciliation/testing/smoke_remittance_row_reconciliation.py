"""Disposable-site regression: release one row and keep all other cash intact."""
import json
from unittest.mock import patch

import frappe
from credinomina_reconciliation.deposit_reconciliation import reconcile_deposit
from credinomina_reconciliation.remittance_row_reconciliation import unreconcile_row, split_row_allocations
from credinomina_reconciliation.rounding import money


def run():
    if frappe.local.site != "cn-reconciliation-test.local":
        raise RuntimeError("Solo para el sitio desechable de pruebas.")
    frappe.set_user("Administrator")
    try:
        with patch.object(frappe, "enqueue"), patch.object(frappe, "publish_realtime"):
            for fifo in (0, 1):
                label="ROW-"+frappe.generate_hash(length=8)
                company=frappe.get_doc(dict(doctype="CN Employer",employer_name=label,employer_code=label)).insert()
                clients=[frappe.get_doc(dict(doctype="CN Client",client_number=label+str(i),client_name=label+str(i),employer=company.name)).insert() for i in range(2)]
                period=frappe.get_doc(dict(doctype="CN Reconciliation Period",employer=company.name,payroll_month="2025-04-01",reconciliation_mode="Historica")).insert()
                imported=frappe.get_doc(dict(doctype="CN Accounting Import",employer=company.name,status="Importado",source_file=f"/private/files/{label}.csv",historical_backfill=1,historical_period=period.name,
                    rows=[dict(source_row=i+2,source_key=label+str(i),event_type="Aplicacion",event_date="2025-04-30",client_name=c.client_name,client_number=c.name,client=c.name,loan_number=label+str(i),reference=label,
                        currency="USD",amount=amount,amount_usd=amount,processing_route="Historica",historical_period=period.name,effective=1,match_status="Pendiente") for i,(c,amount) in enumerate(zip(clients,(100,40)))])).insert()
                from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_accounting_import.cn_accounting_import import _reconcile_sources
                _reconcile_sources(company.name,preserve_deposits=True)
                deposit=frappe.get_doc(dict(doctype="CN Remittance Allocation",employer=company.name,deposit_reference=label,deposit_date="2025-05-10",deposit_currency="USD",deposit_amount=100,
                    detail_file=f"/private/files/{label}.xlsx",detail_source_file=f"/private/files/{label}.xlsx",detail_hash="TEST",apply_fifo=fifo,detail_periods=[dict(period=period.name)],
                    detail_rows=[dict(client_name=c.client_name,client_number=c.name,client=c.name,employer=company.name,loan_number=label+str(i),deducted_usd=amount,amount_usd=amount) for i,(c,amount) in enumerate(zip(clients,(60,40)))])).insert()
                deposit.submit(); reconcile_deposit(deposit); deposit.reload()
                assert money(deposit.allocated_usd)==100, deposit.as_dict()
                other_deposit=frappe.get_doc(dict(doctype='CN Remittance Allocation',employer=company.name,
                    deposit_reference=label+'OTHER',deposit_date='2025-05-11',deposit_currency='USD',deposit_amount=40,
                    detail_file=f'/private/files/{label}OTHER.xlsx',detail_source_file=f'/private/files/{label}OTHER.xlsx',detail_hash='TEST',
                    detail_periods=[dict(period=period.name)],detail_rows=[dict(client_name=clients[0].client_name,
                    client_number=clients[0].name,client=clients[0].name,employer=company.name,loan_number=label+'0',deducted_usd=40,amount_usd=40)])).insert()
                other_deposit.append('targets',dict(historical_application=imported.rows[0].name,amount_usd=40,detail_row=other_deposit.detail_rows[0].name))
                other_deposit.save();other_deposit.submit();reconcile_deposit(other_deposit);other_deposit.reload()
                assert money(other_deposit.allocated_usd)==40
                other_deposit_journal=other_deposit.allocation_detail
                selected=deposit.detail_rows[0].name
                removed,retained=split_row_allocations(deposit,selected)
                assert sum(money(x['importe_usd']) for x in removed)==60
                other_before=deposit.detail_rows[1].as_dict()
                if not fifo:
                    from credinomina_reconciliation.conciliacion_credinomina.doctype.cn_reconciliation_period.cn_reconciliation_period import close_period, reopen_period
                    close_period(period.name)
                    deposit.reload()
                    journal=deposit.allocation_detail
                    try:
                        unreconcile_row(deposit.name,selected,str(deposit.modified))
                    except frappe.ValidationError as error:
                        assert 'Reabra' in str(error), str(error)
                    else:
                        raise AssertionError('A closed period allowed cash to be released')
                    deposit.reload()
                    assert deposit.allocation_detail==journal and money(deposit.allocated_usd)==100
                    reopen_period(period.name,'Prueba de desconciliación por fila')
                result=unreconcile_row(deposit.name,selected,str(deposit.modified))
                deposit.reload();imported.reload()
                assert result['released_usd']==60
                assert money(deposit.allocated_usd)==40 and money(deposit.unallocated_usd)==60
                assert json.loads(deposit.allocation_detail)==retained
                assert deposit.detail_rows[0].manual_reconciliation_required==1
                for field in ('matched_targets','linked_usd','pending_usd','match_status'):
                    assert deposit.detail_rows[1].get(field)==other_before[field], field
                assert money(imported.rows[0].historical_balance_usd)==60
                assert money(imported.rows[1].historical_balance_usd)==0
                other_deposit.reload()
                assert other_deposit.allocation_detail==other_deposit_journal and money(other_deposit.allocated_usd)==40
                reconcile_deposit(deposit);deposit.reload()
                assert money(deposit.allocated_usd)==40
                assert deposit.detail_rows[0].match_status=='Pendiente manual'
                deposit.append('targets',dict(historical_application=imported.rows[0].name,amount_usd=60,detail_row=selected))
                deposit.save();reconcile_deposit(deposit);deposit.reload()
                assert money(deposit.allocated_usd)==100
                assert deposit.detail_rows[0].match_status=='Conciliada'
                assert deposit.detail_rows[0].manual_reconciliation_required==1
                # Imported core evidence can also be chosen by mistake. Its
                # cash balance must reopen without canceling the source item.
                selected_core=deposit.detail_rows[1].name
                unreconcile_row(deposit.name,selected_core,str(deposit.modified));deposit.reload()
                core=frappe.get_doc(dict(doctype='CN Complementary Item',name=label+'CORE',docstatus=1,
                    category='Otros ingresos',employer=company.name,posting_date='2025-05-10',
                    amount_usd=40,amount=40,currency='USD',accounting_source_key=label+'CORE',
                    period=period.name,reference=label,client_number=clients[1].name,loan_number=label+'1'))
                core.db_insert()
                deposit.append('targets',dict(complementary_item=core.name,amount_usd=40,detail_row=selected_core))
                deposit.save();reconcile_deposit(deposit);deposit.reload()
                assert money(deposit.allocated_usd)==100
                assert any(e.get('partida')==core.name for e in json.loads(deposit.allocation_detail))
                released_core=unreconcile_row(deposit.name,selected_core,str(deposit.modified))
                from credinomina_reconciliation.complementary_state import flush
                flush();core.reload();deposit.reload()
                assert released_core['released_usd']==40 and money(deposit.allocated_usd)==60
                assert core.docstatus==1 and money(core.amount_usd)==40 and money(core.pending_usd)==40
                assert money(core.applied_usd)==0
            return dict(fifo_and_exact=True,closed_period_protected=True,other_row_preserved=True,other_deposit_preserved=True,balances_rebuilt=True,no_automatic_reselection=True,manual_relink=True,core_balance_reopened=True,rolled_back=True)
    finally:
        frappe.db.rollback()
