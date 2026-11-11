"""Current/cutoff/export client remainders with company-only differences."""
import json
from io import BytesIO
from unittest.mock import patch

import frappe
from openpyxl import load_workbook

from credinomina_reconciliation.client_position import FINANCIAL_VIEW
from credinomina_reconciliation.operational_statement_export import build_export, REPORT
from credinomina_reconciliation.rounding import money, sum_money
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report


def run():
    if frappe.local.site != 'cn-reconciliation-test.local':
        raise RuntimeError('Solo para cn-reconciliation-test.local')
    frappe.set_user('Administrator')
    marker = 'DEP-POS-' + frappe.generate_hash(length=8)
    created = []
    def insert(doctype, suffix, **fields):
        doc = frappe.get_doc(dict(doctype=doctype, name=marker + suffix, **fields))
        doc.db_insert()
        created.append((doctype, doc.name))
        return doc.name
    try:
        company = insert('CN Employer', '-E', employer_name=marker, employer_code=marker)
        period = insert('CN Reconciliation Period', '-P', employer=company, payroll_month='2025-07-01',
            reconciliation_mode='Historica', cash_breakdown_known=1, deposit_coverage_usd=9353.84)
        imp = insert('CN Accounting Import', '-I', employer=company, currency='USD', status='Importado',
            historical_backfill=1, historical_period=period)
        for suffix, day, amount, paid, clients in [
            ('-D1', '2025-07-08', 4952.45, 4896.06,
                [('4670', 'Jonathan', 13.16), ('2491', 'Marcial', 17.68), ('4122', 'Marlon', 25.56)]),
            ('-D2', '2025-08-20', 4569.18, 4457.78,
                [('4838', 'Alvaro', 22.68), ('8', 'Otro cliente', 88.77)]),
        ]:
            app, detail = marker + suffix + '-A', marker + suffix + '-R'
            ledger = [dict(tipo='Aplicacion historica', periodo=period, aplicacion_id=app,
                fila_detalle=detail, importe_usd=paid)]
            dep = insert('CN Remittance Allocation', suffix, employer=company, docstatus=1, deposit_date=day,
                deposit_currency='USD', deposit_amount=amount, amount_usd=amount, allocated_usd=paid,
                unclassified_usd=float(money(amount) - money(paid)), allocation_detail=json.dumps(ledger),
                difference_notes='Nota de empresa ' + suffix)
            insert('CN Source Row', suffix + '-A', parent=imp, parenttype='CN Accounting Import', parentfield='rows',
                event_type='Aplicacion', effective=1, currency='USD', amount=paid, event_date=day,
                historical_period=period, match_status='Conciliado', client='9', historical_remitted_usd=paid,
                historical_detail=json.dumps([dict(deposito=dep, importe_usd=paid)]))
            insert('CN Remittance Detail', suffix + '-R', parent=dep, parenttype='CN Remittance Allocation',
                parentfield='detail_rows', client_number='9', amount_usd=paid, linked_usd=paid)
            for client, name, pending in clients:
                insert('CN Remittance Detail', suffix + '-' + client, parent=dep, parenttype='CN Remittance Allocation',
                    parentfield='detail_rows', client_number=client, client_name=name, loan_number=client + '-1',
                    amount_usd=pending, pending_usd=pending, difference_notes='Nota cliente ' + client)
        before = [frappe.db.get_value(dt, name, '*', as_dict=True) for dt, name in created]
        original_sql = frappe.db.sql
        def read(query, *args, **kwargs):
            assert not str(query).lstrip().lower().startswith(('insert ', 'update ', 'delete ', 'replace '))
            return original_sql(query, *args, **kwargs)
        with patch.object(frappe.db, 'sql', side_effect=read):
            for cutoff in ('', '2026-08-31'):
                filters = dict(position_type=FINANCIAL_VIEW, employer=company, only_open=1, cutoff_date=cutoff)
                columns, rows, _, _, figures = report.execute(filters)
                assert columns == report.get_columns(filters)
                assert len(rows) == 8 and rows[-1]['deposit_pending_usd'] == 167.79
                assert rows[-1]['net_balance_usd'] == -167.79
                assert sum_money(r['net_balance_usd'] for r in rows[:-1]) == money(-167.79)
                assert next(f['value'] for f in figures if f['label'] == 'Saldo neto de conciliación US$') == -167.79
                assert {row.get('client_number'): row['deposit_pending_usd'] for row in rows[:-1] if row.get('client_number')} == {
                    '4670': 13.16, '2491': 17.68, '4122': 25.56, '4838': 22.68, '8': 88.77}
                differences = [r for r in rows if r.get('category') == 'Diferencia de empresa']
                assert {r['source_document']: r['deposit_pending_usd'] for r in differences} == {
                    marker + '-D1': -.01, marker + '-D2': -.05}
                for row in differences:
                    assert row['employer'] == company and not row.get('client_number') and not row.get('loan_number')
                    assert row['difference_notes'] == 'Nota de empresa ' + row['source_document'].removeprefix(marker)
                for suffix, expected in [('-D1', 56.39), ('-D2', 111.40)]:
                    assert sum_money(r['deposit_pending_usd'] for r in rows[:-1] if r['source_document'] == marker + suffix) == money(expected)
                client = report.execute(dict(filters, client_number='4670'))[1]
                assert len(client) == 2 and client[-1]['deposit_pending_usd'] == 13.16
                assert client[0]['net_balance_usd'] == client[-1]['net_balance_usd'] == -13.16
                assert report.execute(dict(filters, detailed_columns=1))[1] == rows
                _, content = build_export(frappe._dict(filters=filters, ignore_visible_idx=True))
                book = load_workbook(BytesIO(content), data_only=True)
                values = {a: b for a, b in book['Resumen del saldo'].values if a}
                assert values['Depósitos pendientes US$ (se restan)'] == 167.79
                assert values['Saldo neto de conciliación US$'] == -167.79
                sheet = book[REPORT]
                header = [cell.value for cell in sheet[1]]
                assert header == [col['label'] for col in columns if not col.get('hidden')]
                amounts = [row[header.index('Saldo pendiente del depósito US$')] for row in list(sheet.values)[1:-1]]
                assert sorted(v for v in amounts if v < 0) == [-.05, -.01]
                assert sum_money(amounts) == money(167.79)
                net = [row[header.index('Saldo Neto U$')] for row in list(sheet.values)[1:]]
                assert sum_money(net[:-1]) == money(net[-1]) == money(-167.79)
            july = report.execute(dict(filters, cutoff_date='2025-07-31'))[1]
            assert july[-1]['deposit_pending_usd'] == 56.39
            assert {r['source_document'] for r in july[:-1]} == {marker + '-D1'}
        assert before == [frappe.db.get_value(dt, name, '*', as_dict=True) for dt, name in created]
        return dict(current=True, cutoff=True, client_filters=True, company_differences=True,
            unchanged_columns=True, no_duplicated_cash=True, excel=True, read_only=True, rolled_back=True)
    finally:
        frappe.db.rollback()
