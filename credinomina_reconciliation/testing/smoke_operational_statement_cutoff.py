"""Execute historical operational statements against real Frappe; rollback fixtures."""
import json
from unittest.mock import patch

import frappe

from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    marker = 'OP-CUT-' + frappe.generate_hash(length=8)
    company = marker + '-E'
    created = []

    def insert(doctype, suffix, **fields):
        doc = frappe.get_doc(dict(doctype=doctype, name=marker + suffix, **fields))
        doc.db_insert()
        created.append((doctype, doc.name))
        return doc.name

    try:
        insert('CN Employer', '-E', employer_name=company, employer_code=company)
        historical = insert('CN Reconciliation Period', '-H', employer=company, payroll_month='2025-04-01',
            reconciliation_mode='Historica', deposit_coverage_usd=999, cash_breakdown_known=1)
        operative = insert('CN Reconciliation Period', '-P', employer=company, payroll_month='2025-04-01',
            reconciliation_mode='Operativa', deposit_coverage_usd=999, cash_breakdown_known=1)
        parent = insert('CN Accounting Import', '-I', employer=company, currency='USD', status='Importado',
            historical_backfill=1, historical_period=historical)
        op_parent = insert('CN Accounting Import', '-O', employer=company, currency='USD', status='Importado')
        collection = insert('CN Collection Row', '-R', parent=operative, parenttype='CN Reconciliation Period',
            parentfield='collection_rows', row_key='RK', client='7', client_name='Cliente prueba',
            expected_usd=100, applied_usd=100, remitted_usd=100)
        for suffix, amount, day, imp, period in [('-A', 100, '2025-04-15', parent, historical),
                ('-B', 60, '2025-05-15', parent, historical), ('-C', 40, '2025-04-15', op_parent, ''),
                ('-F', 60, '2025-05-15', op_parent, '')]:
            insert('CN Source Row', suffix, parent=imp, parenttype='CN Accounting Import', parentfield='rows',
                event_type='Aplicacion', effective=1, currency='USD', amount=amount, event_date=day,
                historical_period=period, collection_row_id=collection if not period else '',
                match_status='Conciliado', client='7', client_name='Cliente prueba', loan_number='123-1',
                historical_remitted_usd=999, application_adjustment_usd=20 if suffix == '-A' else 0)

        for suffix, amount, day, app in [('-D1', 30, '2025-04-20', '-A'), ('-D2', 50, '2025-05-15', '-A'),
                                        ('-ADV', 60, '2025-04-20', '-B')]:
            detail = marker + suffix + '-R'
            ledger = [dict(tipo='Aplicacion historica', aplicacion_id=marker + app, periodo=historical,
                fila_detalle=detail, importe_usd=amount)]
            dep = insert('CN Remittance Allocation', suffix, employer=company, docstatus=1, deposit_date=day,
                deposit_currency='USD', deposit_amount=amount, amount_usd=amount, allocated_usd=amount,
                unclassified_usd=0, allocation_detail=json.dumps(ledger))
            insert('CN Remittance Detail', suffix + '-R', parent=dep, parenttype='CN Remittance Allocation',
                parentfield='detail_rows', client_number='7', client_name='Cliente prueba', amount_usd=amount,
                pending_usd=0, linked_usd=amount)
        fifo = [dict(tipo='Cobranza', periodo=operative, fila_id='RK', fila_detalle=marker + '-DF-R', importe_usd=100,
            aplicaciones_fifo=[dict(application_id=marker + '-C', amount_usd=40),
                               dict(application_id=marker + '-F', amount_usd=60)])]
        dep = insert('CN Remittance Allocation', '-DF', employer=company, docstatus=1, deposit_date='2025-04-20',
            deposit_currency='USD', deposit_amount=100, amount_usd=100, allocated_usd=100,
            unclassified_usd=0, allocation_detail=json.dumps(fifo))
        insert('CN Remittance Detail', '-DF-R', parent=dep, parenttype='CN Remittance Allocation',
            parentfield='detail_rows', client_number='7', client_name='Cliente prueba', amount_usd=100,
            pending_usd=0, linked_usd=100)
        insert('CN Complementary Item', '-X', employer=company, docstatus=1, category='Ajuste de aplicación',
            posting_date='2025-05-01', currency='USD', amount=20, amount_usd=20,
            related_import=parent, related_application=marker + '-A', application_adjustment_usd=20)
        before = [frappe.db.get_value(dt, name, '*', as_dict=True) for dt, name in created]
        filters = dict(employer=company, client_number='7', cutoff_date='2025-04-30', only_open=0)
        sql = frappe.db.sql
        def read(query, *args, **kwargs):
            assert not str(query).lstrip().lower().startswith(('insert ', 'update ', 'delete ', 'replace '))
            return sql(query, *args, **kwargs)
        with patch.object(frappe.db, 'sql', side_effect=read):
            april = report.execute(filters)
            assert len(april[1]) == 5
            total = april[1][-1]
            assert (total['applied_usd'], total['remitted_usd'], total['applied_pending_usd'],
                    total['deposit_pending_usd']) == (140, 70, 70, 120), total
            assert report.execute(dict(filters, detailed_columns=1))[1:] == april[1:]
            pending = report.execute(dict(filters, only_open=1))[1]
            assert len(pending) == 4 and pending[-1]['applied_pending_usd'] == 70
            assert report.execute(dict(filters, client_number='8'))[1] == []
            assert report.execute(dict(filters, cutoff_date='2025-04-01'))[1] == []
            may = report.execute(dict(filters, cutoff_date='2025-05-31'))[1]
            total = may[-1]
            assert (total['applied_usd'], total['remitted_usd'], total['applied_pending_usd']) == (240, 240, 0), total
            assert report.execute(dict(filters, cutoff_date='2025-05-31', only_open=1))[1] == []
            collection_rows = report.execute(dict(filters, position_type='Cobranza'))[1]
            assert collection_rows[-1]['quality_applied_usd'] == 40
            assert collection_rows[0]['employee_pending_usd'] == 60
            assert report.execute(filters)[1] == april[1], 'Report refresh reused another cutoff'
        after = [frappe.db.get_value(dt, name, '*', as_dict=True) for dt, name in created]
        assert before == after, 'A report changed a reconciliation'
        return dict(historical_applications=True, operational_fifo=True, client_pending_deposits=True,
            dated_adjustments=True, current_collection_cache_ignored=True, columns_unchanged=True,
            read_only=True, rolled_back=True)
    finally:
        frappe.db.rollback()
