from copy import deepcopy
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation import client_position, reconciliation_report_cash
from credinomina_reconciliation import unallocated_deposit_position
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report
from credinomina_reconciliation.unallocated_deposit_position import build as deposit_position


@pytest.fixture(autouse=True)
def report_user():
    with patch.dict(frappe.session, user='Administrator'):
        yield


def application(name='A', amount=100, **fields):
    return dict(dict(name=name, parent='I', event_type='Aplicacion', effective=1, currency='USD',
        amount=amount, event_date='2026-05-15', match_status='Conciliado', historical_period='P',
        historical_remitted_usd=amount, client='7', client_number='', loan_number='0012427-1'), **fields)


def historical_entry(app, value):
    return dict(tipo='Aplicacion historica', periodo='P', aplicacion_id=app, importe_usd=value)


@pytest.mark.parametrize('position_type', ['Depósito sin asignar', 'Depósito con saldo pendiente'])
def test_report_reads_confirmed_legacy_manual_cash_and_totals_only_the_remaining_cent(position_type):
    deposit = dict(name='D', docstatus=1, amount_usd=29.34, allocated_usd=29.33,
        unclassified_usd=.01, justified_surplus_usd=0, deposit_date='2025-07-15',
        allocation_detail=[dict(historical_entry('A', 29.33), origen='Selección manual')])
    detail = dict(name='R', parent='D', client_number='4414', amount_usd=29.34, linked_usd=29.33, matched_targets='[]')
    target = dict(name='T', parent='D', detail_row='R', historical_application='A', amount_usd=29.33, result='Aplicada')
    before = deepcopy((deposit, detail, target))

    def children(doctype, parents, parenttype, parentfield, *, fields):
        assert parents == ['D'] and parenttype == 'CN Remittance Allocation'
        if doctype == 'CN Remittance Detail':
            assert parentfield == 'detail_rows' and 'linked_usd' in fields
            source = detail
        else:
            assert doctype == 'CN Remittance Target' and parentfield == 'targets'
            source = target
        return [{field: source.get(field) for field in fields}]

    with patch.object(frappe, 'has_permission', return_value=True), \
            patch.object(unallocated_deposit_position, 'records', return_value=[deposit]), \
            patch.object(unallocated_deposit_position, 'child_records', side_effect=children):
        columns, rows, *_ = report.execute(dict(position_type=position_type, client_number='4414', only_open=1))
    assert len(rows) == 2 and rows[-1]['is_total_row']
    assert rows[0]['position_type'] == 'Depósito con saldo pendiente'
    assert next(column for column in columns if column['fieldname'] == 'deposit_pending_usd')['label'] == 'Saldo pendiente del depósito US$'
    assert all(row['deposit_pending_usd'] == row['deposit_detail_pending_usd'] == .01 for row in rows)
    assert (deposit, detail, target) == before


def run_applications(sources, filters=None, *, periods=None, collections=None, deposits=None):
    context = sources, {'I': {'employer': 'E'}}, periods or {'P': {'name': 'P', 'employer': 'E'}}, collections or {}, {'E': {}}
    before = deepcopy(context)
    with patch.object(client_position, 'load_application_context', return_value=context), \
         patch.object(reconciliation_report_cash, 'load_period_deposits', return_value=deposits or {}), \
         patch.object(frappe, 'has_permission', return_value=True):
        result = report.execute(dict({'position_type': 'Aplicación', 'only_open': 0}, **(filters or {})))
    assert context == before
    return result[1]


def test_applied_credit_is_coverage_and_keeps_actual_bank_and_zero_debt():
    app = application(historical_detail=[dict(deposito='D', importe_usd=65),
        dict(origen='Saldo a favor', saldo_a_favor='SF', operacion='OP', importe_usd=35)])
    period = dict(name='P', employer='E', cash_breakdown_known=1, deposit_coverage_usd=65)
    for filters in ({'client_number': '0007'}, {'loan_number': '12427'}, {'loan_number': '012427-1'}):
        row, footer = run_applications([app], filters, periods={'P': period})
        assert row['operational_status'] == 'Conciliado' and '35.00' in row['observation']
        for value in (row, footer):
            assert (value['applied_usd'], value['remitted_usd'], value['applied_pending_usd']) == (100, 65, 0)
    assert not run_applications([app], {'loan_number': '12427-2'})
    assert not run_applications([app], {'only_open': 1})


@pytest.mark.parametrize('cached', [True, False])
def test_shared_offsets_total_once_only_when_all_recipients_are_selected(cached):
    apps = [application(name, amount, client=client) for name, amount, client in
            [('A', 40, '1'), ('B', 60, '2'), ('C', 25, '3')]]
    period = dict(name='P', employer='E', cash_breakdown_known=int(cached),
                  negative_complementary_usd=-10 if cached else 0, deposit_coverage_usd=115 if cached else 0)
    deposits = {'D': dict(allocated_usd=90, allocation_detail=[historical_entry('A', 40), historical_entry('B', 60),
                 dict(tipo='Partida complementaria', partida='X', importe_usd=-10)]),
                'D2': dict(allocated_usd=25, allocation_detail=[historical_entry('C', 25)])}
    rows = run_applications(apps, periods={'P': period}, deposits=deposits)
    assert [row['remitted_usd'] for row in rows] == [None, None, 25, 115]
    assert rows[-1]['applied_usd'] == 125 and rows[-1]['applied_pending_usd'] == 0
    for client, cash in [('1', None), ('3', 25)]:
        customer, footer = run_applications(apps, {'client_number': client}, periods={'P': period}, deposits=deposits)
        assert customer['remitted_usd'] == footer['remitted_usd'] == cash
    assert all('_shared_bank_cash' not in row and '_bank_basis' not in row for row in rows)


def test_legacy_journal_must_match_each_individual_claim_even_if_total_coincides():
    apps = [application('A', 30), application('B', 70)]
    period = dict(name='P', employer='E', cash_breakdown_known=0)
    deposits = {'D': dict(allocated_usd=100, allocation_detail=[historical_entry('A', 70), historical_entry('B', 30)])}
    rows = run_applications(apps, periods={'P': period}, deposits=deposits)
    assert all(row['remitted_usd'] is None for row in rows)
    assert rows[-1]['applied_usd'] == 100


def test_independent_deposits_can_be_totalled_by_customer_in_the_same_period():
    apps = [application(name, amount, client=customer)
            for name, amount, customer in [('A', 20, '1'), ('B', 40, '1'), ('C', 20, '2'), ('D', 20, '2')]]
    deposits = {name: dict(allocated_usd=net, allocation_detail=[historical_entry(left, first),
        historical_entry(right, second), dict(tipo='Partida complementaria', importe_usd=-offset)])
        for name, net, left, first, right, second, offset in [('D1', 55, 'A', 20, 'B', 40, 5), ('D2', 39, 'C', 20, 'D', 20, 1)]}
    period = dict(name='P', employer='E', cash_breakdown_known=1, deposit_coverage_usd=94, negative_complementary_usd=-6)
    for client, expected in [('1', 55), ('2', 39)]:
        first, second, footer = run_applications(apps, {'client_number': client}, periods={'P': period}, deposits=deposits)
        assert first['remitted_usd'] is second['remitted_usd'] is None
        assert footer['remitted_usd'] == expected


def test_explicit_fifo_attributes_offset_only_to_recipient_of_that_deposit():
    sources = [application(name, amount, historical_period=None, collection_row_id='R') for name, amount in [('A', 40), ('B', 60)]]
    period = dict(name='P', employer='E', cash_breakdown_known=1, deposit_coverage_usd=90, negative_complementary_usd=-10)
    def fifo(name, amount):
        return dict(tipo='Cobranza', periodo='P', fila_id='RK', importe_usd=amount,
                    aplicaciones_fifo=[dict(application_id=name, amount_usd=amount)])
    collections = {'R': dict(name='R', row_key='RK', parent='P', applied_usd=100, remittance_detail=[fifo('A', 40), fifo('B', 60)])}
    deposits = {'D1': dict(allocated_usd=30, allocation_detail=[fifo('A', 40), dict(tipo='Partida complementaria', importe_usd=-10)]),
                'D2': dict(allocated_usd=60, allocation_detail=[fifo('B', 60)])}
    rows = run_applications(sources, periods={'P': period}, collections=collections, deposits=deposits)
    assert [r['remitted_usd'] for r in rows] == [30, 60, 90]
    assert all(r['applied_pending_usd'] == 0 for r in rows)


def test_corrupt_historical_evidence_does_not_hide_other_applications_or_create_a_partial_total():
    rows = run_applications([application(historical_detail='{'), application('B', 25)])
    assert rows[0]['operational_status'] == 'Revisar distribución' and rows[0]['remitted_usd'] is None
    assert rows[1]['remitted_usd'] == 25
    assert (rows[-1]['applied_usd'], rows[-1]['remitted_usd'], rows[-1]['applied_pending_usd']) == (125, None, None)


def test_totals_follow_the_same_row_permissions_as_desk():
    with patch.dict(frappe.session, user='restricted'), \
         patch('frappe.desk.query_report.get_filtered_data', side_effect=lambda dt, cols, rows, user: rows[1:]):
        row, footer = run_applications([application('A', 100), application('B', 25)])
    assert row['source_rows'] == 'B' and footer['applied_usd'] == footer['remitted_usd'] == 25


@pytest.mark.parametrize('paid, expected', [(0, (0, 60)), (50, (None, None)), (100, (60, 0))])
def test_date_range_on_one_application_of_an_operative_claim(paid, expected):
    periods = {'P': dict(name='P', employer='E', reconciliation_mode='Operativa')}
    sources = [application(name, amount, historical_period=None, collection_row_id='R', event_date=when)
               for name, amount, when in [('A', 40, '2026-05-01'), ('B', 60, '2026-05-15')]]
    collections = {'R': dict(name='R', row_key='RK', parent='P', client='7', applied_usd=100,
                             remittance_detail=[dict(importe_usd=paid)] if paid else [])}
    rows = run_applications(sources, {'from_month': '2026-05-15', 'to_month': '2026-05-15'},
                            periods=periods, collections=collections)
    assert rows[0]['source_rows'] == 'B' and rows[0]['event_date'] == '2026-05-15'
    assert rows[-1]['applied_usd'] == 60
    assert (rows[-1]['remitted_usd'], rows[-1]['applied_pending_usd']) == expected
    all_rows = run_applications(sources, periods=periods, collections=collections)
    assert all_rows[-1]['remitted_usd'] == paid


def test_uncertain_deposit_bank_total_and_beneficiary_filter():
    doc = dict(name='D', employer='PAYER', amount_usd=100, allocated_usd=80, unclassified_usd=20)
    detail = dict(name='R', parent='D', employer='BENEFICIARY', client='7', client_number='', amount_usd=50)
    positions = deposit_position([doc], [detail])
    def totals(filters):
        rows = client_position.build_position([], [], [], {}, [], filters, unallocated_deposits=positions)
        return client_position.summarize_selection(rows)[1]
    assert totals({})['deposit_pending_usd'] == 20
    customer = totals({'employer': 'BENEFICIARY', 'client_number': '007'})
    assert customer['deposit_pending_usd'] is None and customer['deposit_detail_pending_usd'] == 50
    assert totals({'employer': 'PAYER'})['deposit_pending_usd'] == 20
    assert not deposit_position([dict(doc, consolidated_deposit='MANUAL')], [detail])


def test_paid_adjustment_receivable_includes_its_identity_reason_and_open_filter():
    item = dict(name='X', employer='E', period='P', client='7', national_id='ID', loan_number='0012427-1',
                difference_reason='Documentada', subcategory_effect='CxC al Empleado', receivable_usd=0, receivable_status='Cobrada')
    filters = dict(position_type='CxC por ajuste', national_id='id', collection_cycle='Segunda quincena')
    args = dict(adjustment_receivables=[item], period_cycles={'P': 'Segunda quincena'})
    rows = client_position.build_position([], [], [], {}, [], filters, **args)
    assert len(rows) == 1 and rows[0]['difference_reason'] == 'Documentada'
    assert client_position.summarize_selection(rows)[1]['employee_receivable_usd'] == 0
    assert not client_position.build_position([], [], [], {}, [], dict(filters, only_open=1), **args)


def test_missing_collection_conversion_stays_unknown_in_table_total():
    period = frappe._dict(name='P', employer='E', payroll_month='2026-05-01')
    row = frappe._dict(parent='P', name='R', expected_nio=367, expected_usd=0, applied_usd=10)
    with patch.object(report, 'get_periods', return_value=[period]), patch.object(report, 'child_records', return_value=[row]):
        data = report.execute({'position_type': 'Cobranza', 'only_open': 1})[1]
    for line in data:
        assert line['expected_usd'] is None and line['quality_difference_usd'] is None
        assert line['quality_applied_usd'] == 10
