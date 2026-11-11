"""Historical operational balances use dated evidence without changing reconciliations."""
from copy import deepcopy
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation import historical_cutoff, operational_statement_cutoff as cutoff
from credinomina_reconciliation import reconciliation_report_cash
from credinomina_reconciliation.company_conversion import journal_entry
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report


def application(name='A', amount=100, **values):
    return dict(dict(name=name, parent='I', event_type='Aplicacion', effective=1, currency='USD', amount=amount,
        event_date='2025-04-15', historical_period='P', match_status='Conciliado', client='7', client_name='Ana',
        loan_number='123-1', historical_remitted_usd=999), **values)


def context(sources=None, **values):
    return (sources if sources is not None else [application()], {'I': dict(name='I', employer='E')},
        {'P': dict(name='P', employer='E', reconciliation_mode=values.get('mode', 'Historica'),
            payroll_month='2025-04-01', cash_breakdown_known=1, deposit_coverage_usd=999)},
        values.get('collections', {}), {'E': {}})


def entry(app='A', amount=100, **values):
    return dict(tipo='Aplicacion historica', aplicacion_id=app, periodo='P', importe_usd=amount, **values)


def deposit(name='D', amount=100, ledger=None, **values):
    return dict(dict(name=name, employer='E', docstatus=1, deposit_date='2025-04-20', amount_usd=amount,
        allocated_usd=999, justified_surplus_usd=999, unclassified_usd=999,
        allocation_detail=ledger if ledger is not None else [entry(amount=amount)]), **values)


def item(name='X', amount=20, **values):
    return dict(dict(name=name, employer='E', docstatus=1, category='Ajuste de conciliación',
        posting_date='2025-04-15', amount_usd=amount, client_number='7'), **values)


def execute_at(ctx=None, deposits=(), items=(), offsets=(), day='2025-04-30', details=(), filters=None):
    inputs = (ctx or context(), deposits, items, offsets)
    original = deepcopy(inputs)
    snapshot = historical_cutoff.reconstruct(*inputs, day, include_position=True)
    before = deepcopy(snapshot)
    with patch.dict(frappe.session, user='Administrator'), \
            patch.object(cutoff, 'load_snapshot', return_value=snapshot), \
            patch.object(cutoff, 'records', return_value=[]), \
            patch.object(cutoff, 'child_records', return_value=details), \
            patch.object(cutoff, 'load_manual_targets', return_value=[]), \
            patch.object(frappe, 'has_permission', return_value=True), \
            patch('frappe.utils.formatdate', side_effect=str), \
            patch.object(reconciliation_report_cash, 'load_period_deposits', side_effect=AssertionError('Read current cash')), \
            patch.object(report, 'load_position', side_effect=AssertionError('Read current balances')):
        result = report.execute(dict({'cutoff_date': day, 'only_open': 0}, **(filters or {})))
    assert snapshot == before and inputs == original
    assert result[0] == report.get_columns(filters)
    return result


def test_later_deposits_and_adjustments_reopen_application_then_settle_at_their_dates():
    ctx = context([application(application_adjustment_usd=20)])
    deposits = [deposit('D1', 30), deposit('D2', 50, deposit_date='2025-05-15')]
    items = [item(category='Ajuste de aplicación', related_application='A', application_adjustment_usd=20,
                  posting_date='2025-05-01')]
    result = execute_at(ctx, deposits, items)
    row, total = result[1]
    assert (row['applied_usd'], row['remitted_usd'], row['applied_pending_usd']) == (100, 30, 70)
    assert total['applied_pending_usd'] == 70 and '2025' in result[2] and 'vínculos vigentes' in result[2]
    for expanded in (0, 1):
        later = execute_at(ctx, deposits, items, day='2025-05-15', filters={'detailed_columns': expanded})
        row = next(r for r in later[1] if r.get('position_type') == 'Aplicación')
        assert (row['applied_usd'], row['remitted_usd'], row['applied_pending_usd']) == (80, 80, 0)
    assert not execute_at(ctx, deposits, items, day='2025-05-15', filters={'only_open': 1})[1]


def test_advance_cash_is_attributed_to_detail_until_application_exists_without_current_credit_cache():
    ctx = context([application(event_date='2025-05-15')])
    dep = deposit(ledger=[entry(fila_detalle='R')])
    details = [dict(name='R', parent='D', client_number='7', client_name='Ana', amount_usd=100,
                    client_credit_usd=100, pending_usd=0)]
    # The loader selects only identity and amounts, then replaces reservation caches.
    credit = item(category='Saldo a favor del cliente', result='Saldo a favor documentado', registered_deposit='D',
                  credit_detail_row='R', posting_date='2025-05-01', credit_resolved_usd=0, credit_history=[])
    row, total = execute_at(ctx, [dep], [credit], details=details, filters={'client_number': '0007'})[1]
    assert row['position_type'] == 'Depósito con saldo pendiente' and row['source_rows'] == 'R'
    assert row['deposit_pending_usd'] == total['deposit_pending_usd'] == 100
    at_credit = execute_at(ctx, [dep], [credit], day='2025-05-01', details=details)[1]
    pending = next(r for r in at_credit if r.get('position_type') == 'Depósito con saldo pendiente')
    assert pending['deposit_pending_usd'] == 80


def test_fifo_spanning_cutoff_preserves_only_the_executed_application_slices():
    apps = [application('A', 40, historical_period='', collection_row_id='R'),
            application('B', 60, historical_period='', collection_row_id='R', event_date='2025-05-15')]
    collections = {'R': dict(name='R', parent='P', row_key='RK', client='7', applied_usd=100,
                              remittance_detail='stale cache')}
    ledger = [dict(tipo='Cobranza', periodo='P', fila_id='RK', importe_usd=100,
                   aplicaciones_fifo=[dict(application_id='A', amount_usd=40), dict(application_id='B', amount_usd=60)])]
    ctx = context(apps, mode='Operativa', collections=collections)
    rows = execute_at(ctx, [deposit(ledger=ledger)], filters={'position_type': 'Aplicación'})[1]
    assert len(rows) == 2 and rows[0]['source_rows'] == 'A'
    assert (rows[0]['applied_usd'], rows[0]['remitted_usd'], rows[0]['applied_pending_usd']) == (40, 40, 0)
    later = execute_at(ctx, [deposit(ledger=ledger)], day='2025-05-31', filters={'position_type': 'Aplicación'})[1]
    assert [(r['source_rows'], r['remitted_usd']) for r in later[:-1]] == [('A', 40), ('B', 60)]


@pytest.mark.parametrize('company_difference', [False, True])
def test_shared_signed_funding_never_invents_individual_bank_cash(company_difference):
    apps = [application('A', 40, client='7'), application('B', 60, client='8')]
    if company_difference:
        adjustment = journal_entry(dict(deposit='D', employer='E', period='P', difference_row='DIFF',
            amount_usd=-.01, application_ids=['A', 'B'], difference_reason='Diferencia Inmaterial'))
        amount, items = 99.99, []
    else:
        adjustment = dict(tipo='Partida complementaria', partida='X', periodo='P', importe_usd=-10)
        amount, items = 90, [item(amount=-10)]
    dep = deposit(amount=amount, ledger=[entry('A', 40), entry('B', 60), adjustment])
    filters = {'position_type': 'Aplicación'}
    rows = execute_at(context(apps), [dep], items, filters=filters)[1]
    assert [r['remitted_usd'] for r in rows] == [None, None, amount]
    assert rows[-1]['applied_usd'] == 100 and rows[-1]['applied_pending_usd'] == 0
    customer = execute_at(context(apps), [dep], items, filters=dict(filters, client_number='7'))[1]
    assert customer[-1]['remitted_usd'] is None


def test_credit_application_uses_its_date_and_does_not_become_new_bank_cash():
    credit = item(amount=35, category='Saldo a favor del cliente', result='Saldo a favor documentado',
        registered_deposit='ORIGIN', credit_resolved_usd=35,
        credit_history=[dict(operation_id='USE', application_id='A', fecha='2025-05-01', importe_usd=35)])
    deposits = [deposit('ORIGIN', 35, []), deposit(amount=65)]
    for day, pending in [('2025-04-30', 35), ('2025-05-01', 0)]:
        rows = execute_at(deposits=deposits, items=[credit], day=day, filters={'position_type': 'Aplicación'})[1]
        assert rows[0]['remitted_usd'] == rows[-1]['remitted_usd'] == 65
        assert rows[0]['applied_pending_usd'] == pending


@pytest.mark.parametrize('day,remaining', [('2025-04-30', 20), ('2025-05-01', 5), ('2025-06-01', 10)])
def test_compensation_and_reversal_are_dated(day, remaining):
    complementary = item(category='Compensación entre partidas', compensated_usd=10)
    offsets = [dict(parent='X', compensation_date='2025-05-01', amount_usd=15),
               dict(parent='X', compensation_date='2025-06-01', amount_usd=-5)]
    rows = execute_at(items=[complementary], offsets=offsets, day=day,
                      filters={'position_type': 'Partida complementaria'})[1]
    assert rows[0]['complementary_pending_usd'] == rows[-1]['complementary_pending_usd'] == remaining


def test_collection_uses_projected_applied_instead_of_current_cache():
    period = frappe._dict(name='P', employer='E', payroll_month='2025-04-01')
    row = frappe._dict(name='R', parent='P', client_number='7', expected_usd=100, applied_usd=100)
    filters = frappe._dict(position_type='Cobranza', cutoff_date='2025-04-30', only_open=1)
    with patch.object(report, 'get_periods', return_value=[period]), \
            patch.object(report, 'child_records', return_value=[row]):
        _, data = report._collection_report(filters, {'R': {'applied_usd': 40}})
    assert data[0]['quality_applied_usd'] == 40 and data[0]['employee_pending_usd'] == 60
    assert row.applied_usd == 100


def test_earlier_core_item_does_not_disappear_before_later_credit_origin():
    credit = item(category='Saldo a favor del cliente', result='Saldo a favor documentado',
        accounting_source_key='K', source_date='2025-04-01', posting_date='2025-04-30', credit_origin_date='2025-04-30',
        reclassification_date='2025-04-30', registered_deposit='D', credit_history=[], credit_resolved_usd=0)
    dep = deposit(amount=20, ledger=[], deposit_date='2025-04-30')
    rows = execute_at(deposits=[dep], items=[credit], day='2025-04-15',
                      filters={'position_type': 'Partida complementaria', 'only_open': 1})[1]
    assert rows[0]['source_document'] == 'X'
    assert str(rows[0]['event_date']) == '2025-04-01'
    assert rows[0]['complementary_pending_usd'] == rows[-1]['complementary_pending_usd'] == 20
    assert rows[0]['operational_status'] == 'Pendiente'


def test_future_classification_keeps_original_core_date_and_pending_balance():
    adjustment = item(category='Ajuste de aplicación', related_application='A', application_adjustment_usd=20,
        source_date='2025-04-01', posting_date='2025-05-01', accounting_source_key='K')
    rows = execute_at(items=[adjustment], filters={'position_type': 'Partida complementaria'})[1]
    assert str(rows[0]['event_date']) == '2025-04-01'
    assert rows[0]['category'] == 'Por clasificar'
    assert rows[0]['complementary_pending_usd'] == 20


def test_collection_periods_after_cutoff_are_excluded():
    periods = [frappe._dict(name='P', payroll_month='2025-04-01'), frappe._dict(name='F', payroll_month='2025-05-01')]
    with patch.object(report, 'records', return_value=periods):
        assert report.get_periods(frappe._dict(cutoff_date='2025-04-30')) == periods[:1]


@pytest.mark.parametrize('extra', [dict(cutoff_date='2999-01-01'), dict(from_month='2025-05-01'),
                                  dict(to_month='2025-05-01'), dict(from_month='2025-04-20', to_month='2025-04-01')])
def test_invalid_cutoffs_are_rejected_before_reading_balances(extra):
    with patch.object(frappe, 'has_permission', return_value=True), \
            patch.object(historical_cutoff, 'records', side_effect=AssertionError('Read before validation')):
        with pytest.raises(frappe.ValidationError):
            cutoff.load_snapshot(dict({'cutoff_date': '2025-04-30'}, **extra))


def test_empty_cutoff_uses_current_report_without_loading_a_projection():
    with patch.dict(frappe.session, user='Administrator'), \
            patch.object(report, 'load_position', return_value=[]) as current, \
            patch.object(cutoff, 'load_snapshot', side_effect=AssertionError('Historical load with no cutoff')):
        result = report.execute({'cutoff_date': ''})
    assert current.call_count == 1 and 'Los saldos son actuales.' in result[2]
