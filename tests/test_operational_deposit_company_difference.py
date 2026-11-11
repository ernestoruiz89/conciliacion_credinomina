from copy import deepcopy
from io import BytesIO
from unittest.mock import patch

import frappe
import pytest
from openpyxl import load_workbook

from credinomina_reconciliation import client_position as position
from credinomina_reconciliation import operational_statement_export as export
from credinomina_reconciliation import unallocated_deposit_position as deposits
from credinomina_reconciliation.rounding import money, sum_money
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report


def example(*, zero_cash=False):
    parent = dict(name='DEP', employer='E', docstatus=1, deposit_date='2025-07-08', amount_usd=4952.45,
        allocated_usd=4896.06, justified_surplus_usd=0, unclassified_usd=56.39,
        difference_reason='Diferencia documentada', difference_notes='Nota de empresa',
        allocation_detail=[dict(tipo='Aplicacion historica', aplicacion_id='A', fila_detalle='PAID', importe_usd=4896.06)])
    rows = [dict(name='PAID', parent='DEP', client_number='9', amount_usd=4896.06)]
    rows.extend(dict(name=name, parent='DEP', client_number=client, client_name=name,
        loan_number=client + '-1', amount_usd=amount, difference_reason='Motivo ' + name,
        difference_notes='Nota ' + name) for name, client, amount in [
            ('JONATHAN', '4670', 13.16), ('MARCIAL', '2491', 17.68), ('MARLON', '4122', 25.56)])
    if zero_cash:
        parent.update(amount_usd=4896.06, unclassified_usd=0)
    return parent, rows


def build(parent, details):
    before = deepcopy((parent, details))
    rows = deposits.build([parent], details, separate_company_difference=True)
    assert (parent, details) == before
    return rows


def test_proven_clients_keep_their_balances_and_company_difference_counts_cash_once():
    parent, details = example()
    rows = build(parent, details)
    assert [(r['client_name'], r['deposit_pending_usd']) for r in rows] == [
        ('JONATHAN', 13.16), ('MARCIAL', 17.68), ('MARLON', 25.56), ('Diferencia de empresa', -.01)]
    assert sum_money(r['deposit_pending_usd'] for r in rows) == money(56.39)
    assert rows[-1]['operational_status'] == 'Revisar distribución'
    assert not any(rows[-1].get(field) for field in ('client_number', 'national_id', 'loan_number', 'source_rows'))
    assert rows[-1]['difference_reason'] == 'Diferencia documentada'
    assert rows[-1]['difference_notes'] == 'Nota de empresa'
    assert rows[0]['difference_notes'] == 'Detalle: Nota JONATHAN\nDepósito: Nota de empresa'
    amounts = position.summarize_selection(rows)[1]
    assert amounts['deposit_pending_usd'] == 56.39 and amounts['deposit_detail_pending_usd'] == 56.40
    figures = position.net_balance_summary(rows, amounts, dict(position_type=position.FINANCIAL_VIEW))
    assert figures[-1]['value'] == -56.39
    # The shared helper's other report callers retain their existing presentation.
    previous = deposits.build([parent], details)
    assert previous[0]['deposit_pending_usd'] is None and previous[-1]['deposit_pending_usd'] == 56.39


@pytest.mark.parametrize('cash, pending, difference', [(111.40, 111.45, -.05), (20, 50, -30), (0, 50, -50)])
def test_difference_is_not_a_tolerance_or_a_proportional_client_haircut(cash, pending, difference):
    parent = dict(name='DEP', employer='E', amount_usd=cash, allocated_usd=0, unclassified_usd=cash)
    details = [dict(name='R', parent='DEP', client_number='7', amount_usd=pending)]
    rows = build(parent, details)
    assert [r['deposit_pending_usd'] for r in rows] == [pending, difference]
    assert position.summarize_selection(rows)[1]['deposit_pending_usd'] == cash


@pytest.mark.parametrize('fault', ['missing_row', 'total', 'overapplied', 'overreserved', 'cache', 'corrupt'])
def test_incomplete_or_conflicting_evidence_does_not_create_a_company_difference(fault):
    parent, details = example()
    if fault == 'missing_row':
        parent['allocation_detail'][0].pop('fila_detalle')
    elif fault == 'total':
        parent['allocation_detail'][0]['importe_usd'] = 4896.05
    elif fault == 'overapplied':
        details[0]['amount_usd'] = 4896.05
    elif fault == 'overreserved':
        details[1]['client_credit_usd'] = 1
    elif fault == 'cache':
        parent['unclassified_usd'] = 56.40
    else:
        parent['allocation_detail'] = '{'
    rows = build(parent, details)
    assert rows[0]['deposit_pending_usd'] is None
    assert rows[-1]['deposit_pending_usd'] == 56.39
    assert not any(row['client_name'] == 'Diferencia de empresa' for row in rows)


def test_anonymous_details_and_documented_credits_are_not_lost_or_duplicated():
    parent = dict(name='DEP', employer='E', amount_usd=100, allocated_usd=30, justified_surplus_usd=10,
        unclassified_usd=60, allocation_detail=[dict(tipo='Aplicacion historica', fila_detalle='R', importe_usd=30)])
    details = [dict(name='R', parent='DEP', client_number='7', amount_usd=80, client_credit_usd=10),
               dict(name='ANON', parent='DEP', amount_usd=25)]
    rows = build(parent, details)
    assert [r['deposit_pending_usd'] for r in rows] == [40, 25, -5]
    assert rows[1]['client_name'] == 'Sin cliente individual'
    assert sum_money(r['deposit_pending_usd'] for r in rows) == money(60)


def test_company_difference_stays_on_payer_and_is_never_charged_to_filtered_client():
    parent, details = example()
    details[1]['employer'] = 'BENEFICIARY'
    rows = build(parent, details)
    def selected(filters):
        return position.build_position([], [], [], {}, [], dict(only_open=1, **filters), unallocated_deposits=rows)
    client = selected({'client_number': '004670'})
    assert len(client) == 1 and client[0]['deposit_pending_usd'] == 13.16
    assert position.summarize_selection(selected({'employer': 'BENEFICIARY'}))[1]['deposit_pending_usd'] == 13.16
    assert position.summarize_selection(selected({'employer': 'E'}))[1]['deposit_pending_usd'] == 43.23
    assert selected({'loan_number': '4670-1'}) == client


@pytest.mark.parametrize('zero_cash', [False, True])
def test_current_report_loader_totals_and_excel_preserve_signed_company_line_without_new_columns(zero_cash):
    parent, details = example(zero_cash=zero_cash)
    original = deepcopy((parent, details))
    expected = 0 if zero_cash else 56.39
    expected_difference = -56.40 if zero_cash else -.01
    filters = dict(position_type='Depósito con saldo pendiente', only_open=1)
    def children(doctype, parents, *args, **kwargs):
        assert parents == ['DEP']
        return details if doctype == 'CN Remittance Detail' else []
    with patch.dict(frappe.session, user='Administrator'), patch.object(frappe, 'has_permission', return_value=True), \
            patch.object(deposits, 'records', return_value=[parent]), patch.object(deposits, 'child_records', side_effect=children):
        columns, rows, *_ = report.execute(filters)
    assert columns == report.get_columns(filters)
    assert rows[-1]['deposit_pending_usd'] == expected
    assert next(r for r in rows if r.get('client_name') == 'Diferencia de empresa')['deposit_pending_usd'] == expected_difference
    from frappe.desk import query_report as native
    source = frappe._dict(columns=report.get_columns(), result=rows)
    with patch.object(native, 'run', return_value=source), \
            patch.object(native, 'formatdate', side_effect=str), \
            patch('frappe.utils.xlsxutils.get_excel_date_format', return_value=('DD/MM/YYYY', 'HH:MM:SS')):
        _, content = export.build_export(frappe._dict(filters=dict(position_type=position.FINANCIAL_VIEW), ignore_visible_idx=True))
    book = load_workbook(BytesIO(content), data_only=True)
    sheet = book[export.REPORT]
    header = [cell.value for cell in sheet[1]]
    assert header == [col['label'] for col in report.get_columns() if not col.get('hidden')]
    column = header.index('Saldo pendiente del depósito US$') + 1
    client_column = header.index('Cliente') + 1
    assert {sheet.cell(i, client_column).value: sheet.cell(i, column).value for i in range(2, 7)} == {
        'JONATHAN': 13.16, 'MARCIAL': 17.68, 'MARLON': 25.56, 'Diferencia de empresa': expected_difference, 'Total': expected}
    values = {a: b for a, b in book['Resumen del saldo'].values if a}
    assert values['Depósitos pendientes US$ (se restan)'] == expected
    assert values['Saldo neto de conciliación US$'] == -expected
    assert (parent, details) == original
