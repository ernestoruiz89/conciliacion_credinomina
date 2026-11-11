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
    assert [r['assigned_usd'] for r in rows] == [40, 60, 100]
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
        assert rows[0]['assigned_usd'] == rows[-1]['assigned_usd'] == 100 - pending


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


def test_net_summary_tracks_dated_cash_and_credit_adjustment_without_changing_evidence():
    adjustment = item(category='Ajuste de aplicación', related_application='A', application_adjustment_usd=20,
                      posting_date='2025-05-01', source_credit=20, source_currency='USD')
    dep = deposit(amount=80, deposit_date='2025-05-15', ledger=[entry(amount=80)])
    for day, expected in [('2025-04-30', 100), ('2025-05-01', 80), ('2025-05-15', 0)]:
        result = execute_at(deposits=[dep], items=[adjustment], day=day)
        assert next(row['value'] for row in result[4] if row['label'] == 'Saldo neto de conciliación US$') == expected


def test_documented_differences_are_carried_to_historical_rows():
    ctx = context([application(difference_reason='Aplicación', difference_notes='Nota aplicación')])
    comp = item(accounting_source_key='KEY', source_credit=20, source_currency='USD',
                difference_reason='Partida', difference_notes='Nota partida')
    dep = deposit(amount=10, ledger=[], difference_reason='Depósito', difference_notes='Nota depósito')
    result = execute_at(ctx, deposits=[dep], items=[comp])
    rows = {r['position_type']: r for r in result[1] if not r.get('is_total_row')}
    for kind, reason, note in [('Aplicación', 'Aplicación', 'Nota aplicación'),
                               ('Partida complementaria', 'Partida', 'Nota partida'),
                               ('Depósito con saldo pendiente', 'Depósito', 'Nota depósito')]:
        assert rows[kind]['difference_reason'] == reason
        assert rows[kind]['difference_notes'] == note


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


@pytest.mark.parametrize('operative,unknown', [(False, False), (True, False), (False, True)])
def test_position_projection_skips_company_presentation_and_reuses_exact_conversions(operative, unknown):
    from credinomina_reconciliation import application_aging, company_statement
    app = application(historical_period='' if operative else 'P', collection_row_id='R' if operative else '')
    if unknown:
        app.update(currency='NIO', equivalent_amount=0, manual_fx_rate=0)
    ctx = context([app], mode='Operativa' if operative else 'Historica',
        collections={'R': dict(name='R', parent='P', row_key='RK', applied_usd=100)} if operative else {})
    original = deepcopy(ctx)
    expected = historical_cutoff.reconstruct(ctx, [], [], [], '2025-04-30', include_position=True)
    with patch.object(company_statement, 'build_detail', side_effect=AssertionError('Unused company rows')), \
            patch.object(company_statement, 'summarize', side_effect=AssertionError('Unused company totals')), \
            patch.object(application_aging, 'converted_amount', side_effect=AssertionError('Converted twice')):
        lean = historical_cutoff.reconstruct(ctx, [], [], [], '2025-04-30', include_position=True, include_statement=False)
    assert {k: lean[k] for k in expected if k not in {'detail', 'summary'}} == {
        k: expected[k] for k in expected if k not in {'detail', 'summary'}}
    assert lean['detail'] == lean['summary'] == [] and ctx == original


@pytest.mark.parametrize('filters', [dict(client_number='0007'), dict(national_id='abc'),
    dict(loan_number='000123'), dict(employer='E'), dict(client_number='7', from_month='2025-04-15'),
    dict(client_number='7', only_open=1), dict(client_number='missing')])
def test_narrowed_presentation_keeps_every_recipient_of_shared_cash(filters):
    apps = [application('A', 40, client='7', national_id='ABC'), application('B', 60, client='8'),
            application('OTHER', 50, historical_period='OTHER', parent='J', client='9', national_id='OTHER')]
    ctx = context(apps)
    ctx[1]['J'] = dict(name='J', employer='F')
    ctx[2]['OTHER'] = dict(name='OTHER', employer='F', reconciliation_mode='Historica', payroll_month='2025-04-01')
    dep = deposit(amount=90, ledger=[entry('A', 40), entry('B', 60),
        dict(tipo='Partida complementaria', partida='X', periodo='P', importe_usd=-10)])
    selected = dict({'position_type': 'Aplicación'}, **filters)
    args = (ctx, [dep], [item(amount=-10)])
    with patch.object(cutoff, '_application_scope', side_effect=lambda apps, _filters: apps):
        expected = execute_at(*args, filters=selected)
    actual = execute_at(*args, filters=selected)
    assert actual == expected
    if filters.get('client_number') == '0007':
        assert actual[1][-1]['remitted_usd'] is None


@pytest.mark.parametrize('inconsistent', [False, True])
def test_deposit_scope_keeps_cross_company_beneficiaries_and_entire_deposit_proof(inconsistent):
    from credinomina_reconciliation.client_position import build_position
    deposits = [deposit('OWN', 25, [], employer='E', allocated_usd=0, justified_surplus_usd=0),
        deposit('PAYER', 100, [entry('OTHER', 30, fila_detalle='R2')], employer='PAYER', allocated_usd=30, justified_surplus_usd=0),
        deposit('UNRELATED', 100, [], employer='Z', allocated_usd=0, justified_surplus_usd=0),
        deposit('OLDER', 50, [], employer='E', deposit_date='2025-03-31', allocated_usd=0, justified_surplus_usd=0)]
    details = [dict(name='R1', parent='PAYER', employer='E', client_number='7', amount_usd=60),
        dict(name='R2', parent='PAYER', employer='PAYER', client_number='8', amount_usd=100 if inconsistent else 40)]
    snapshot = {'position_context': dict(deposits=deposits, credits_by_deposit={})}
    filters = dict(employer='E', from_month='2025-04-01')
    reads = []
    def children(doctype, parents, parenttype, parentfield, *, fields, filters=None):
        assert (doctype, parenttype, parentfield) == ('CN Remittance Detail', 'CN Remittance Allocation', 'detail_rows')
        if fields == ['parent']:
            assert set(parents) == {'PAYER', 'UNRELATED'} and filters == {'employer': 'E'}
            return [dict(parent='PAYER')]
        reads.append(set(parents))
        return [row for row in details if row['parent'] in parents]
    before = deepcopy((snapshot, details))
    with patch.object(cutoff, 'child_records', side_effect=children), \
            patch.object(cutoff, 'load_manual_targets', return_value=[]):
        expected = cutoff.pending_deposits(snapshot)
        rows = cutoff.pending_deposits(snapshot, filters)
    def selected(rows):
        return build_position([], [], [], {}, [], filters, unallocated_deposits=rows)
    assert selected(rows) == selected(expected)
    assert reads[-1] == {'OWN', 'PAYER'}
    customer = next(row for row in selected(rows) if row.get('client_number') == '7')
    assert customer['deposit_pending_usd'] == (None if inconsistent else 60)
    assert (snapshot, details) == before
