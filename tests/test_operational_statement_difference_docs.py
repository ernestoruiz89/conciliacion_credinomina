from copy import deepcopy
from unittest.mock import patch

import frappe

from credinomina_reconciliation import client_position as position
from credinomina_reconciliation.application_aging import application_balances
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.unallocated_deposit_position import build as build_deposits
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report


def test_documentation_visible_in_both_column_modes_without_duplicates():
    for detailed in [0, 1]:
        for view in [position.FINANCIAL_VIEW, 'Aplicación', 'Partida complementaria', 'Depósito con saldo pendiente', 'Cobranza']:
            columns = report.get_columns(dict(detailed_columns=detailed, position_type=view))
            for field in ['difference_reason', 'difference_notes']:
                matches = [col for col in columns if col['fieldname'] == field]
                assert len(matches) == 1 and not matches[0].get('hidden')


def test_source_application_documentation_survives_grouping_and_date_selection():
    sources = [dict(name=name, parent='I', event_type='Aplicacion', effective=1, currency='USD', amount=10,
        event_date=day, match_status='Conciliado', collection_row_id='R', client='7', loan_number='1-1',
        difference_reason=reason, difference_notes=note) for name, day, reason, note in [
            ('A', '2026-05-01', 'Razón A', 'Nota A'), ('B', '2026-05-15', 'Razón B', 'Nota B')]]
    inputs = (sources, {'I': dict(employer='E')}, {'P': dict(name='P', employer='E', payroll_month='2026-05-01')},
              {'R': dict(name='R', parent='P', row_key='RK', applied_usd=20)}, {'E': {}})
    before = deepcopy(inputs)
    rows = application_balances(*inputs, '2026-06-01', include_settled=True, statement_detail=True)
    assert len(rows) == 1
    assert rows[0]['difference_reason'] == 'Razón A · Razón B'
    assert rows[0]['difference_notes'] == 'Nota A\nNota B'
    filtered = position._application_in_range(rows[0], {'from_month': '2026-05-15'})
    assert filtered['difference_reason'] == 'Razón B' and filtered['difference_notes'] == 'Nota B'
    assert inputs == before


def test_complementary_and_receivable_keep_exact_notes_without_changing_balances():
    item = dict(name='X', employer='E', docstatus=1, category='Por clasificar', amount_usd=20,
                difference_reason='Otra causa', difference_notes='<Nota>\nsegunda línea')
    before = deepcopy(item)
    balance = financial_balance(item)
    rows = position.build_position([], [], [item], {'X': balance}, [], {})
    assert rows[0]['difference_reason'] == item['difference_reason']
    assert rows[0]['difference_notes'] == item['difference_notes']
    assert rows[0]['complementary_pending_usd'] == 20 and item == before


def test_deposit_distinguishes_detail_and_header_documentation():
    deposit = dict(name='D', employer='E', amount_usd=40, allocated_usd=0, unclassified_usd=40,
                   allocation_detail=[], difference_reason='Empresa', difference_notes='Nota de la empresa')
    detail = dict(name='R', parent='D', client_number='7', amount_usd=30,
                  difference_reason='Cliente', difference_notes='Nota del cliente')
    before = deepcopy((deposit, detail))
    rows = build_deposits([deposit], [detail])
    assert rows[0]['difference_reason'] == 'Detalle: Cliente\nDepósito: Empresa'
    assert rows[0]['difference_notes'] == 'Detalle: Nota del cliente\nDepósito: Nota de la empresa'
    assert rows[1]['difference_reason'] == 'Empresa' and rows[1]['difference_notes'] == 'Nota de la empresa'
    assert [r['deposit_pending_usd'] for r in rows] == [30, 10]
    assert (deposit, detail) == before


def test_collection_view_reads_its_own_documentation():
    period = frappe._dict(name='P', employer='E', payroll_month='2026-05-01')
    row = frappe._dict(name='R', parent='P', expected_usd=20, applied_usd=10,
                      difference_reason='Pendiente documentado', difference_notes='Nota cobranza')
    with patch.object(report, 'get_periods', return_value=[period]), \
            patch.object(report, 'child_records', return_value=[row]) as query:
        columns, rows = report._collection_report(frappe._dict(position_type='Cobranza'))
    assert {'difference_reason', 'difference_notes'} <= set(query.call_args.kwargs['fields'])
    assert rows[0]['difference_reason'] == 'Pendiente documentado' and rows[0]['difference_notes'] == 'Nota cobranza'
