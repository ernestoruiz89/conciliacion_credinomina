from copy import deepcopy
from unittest.mock import patch

import pytest

from credinomina_reconciliation import company_statement_bridge as bridge
from credinomina_reconciliation.historical_cutoff import reconstruct
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as report


def application(amount=100):
    return dict(name='A', parent='I', employer='E', event_type='Aplicacion', effective=1,
        currency='USD', amount=amount, event_date='2025-04-30', historical_period='H', match_status='Conciliado')


def deposit(amount=100, **changes):
    return dict(dict(name='D', employer='E', docstatus=1, deposit_date='2025-04-30', amount_usd=amount,
        allocation_detail=[dict(tipo='Aplicacion historica', aplicacion_id='A', periodo='H', importe_usd=amount)]), **changes)


def item(name='C', **changes):
    return dict(dict(name=name, employer='E', docstatus=1, posting_date='2025-04-30',
        category='Ajuste de conciliación', amount_usd=-10, accounting_status='Pendiente de registro'), **changes)


def ledger(amount, kind, name, **changes):
    field = {'deposit': 'remittance_allocation', 'item': 'complementary_item', 'application': 'source_row_id'}[kind]
    return dict(dict(employer='E', source_document=name, core_balance_usd=amount, **{field: name}), **changes)


def project(sources=(), deposits=(), items=(), offsets=(), cutoff='2025-05-31'):
    context = (list(sources), {'I': dict(name='I', employer='E')},
        {'H': dict(name='H', employer='E', payroll_month='2025-04-01', reconciliation_mode='Historica')}, {}, {})
    return reconstruct(context, list(deposits), list(items), list(offsets), cutoff, include_bridge=True)


def explain(core, snapshot):
    before = deepcopy((core, snapshot))
    result = bridge.build_bridge(core, snapshot, 'E')
    assert (core, snapshot) == before, 'A report must not change either ledger or projection'
    return result


def changes(rows):
    return [row for row in rows if row['bridge_row_type'] == 'difference']


def test_bank_difference_explains_gap_without_counting_credit_reserve_twice():
    credit = item(category='Saldo a favor del cliente', result='Saldo a favor documentado',
        amount_usd=60.26, registered_deposit='D', credit_resolved_usd=0)
    snapshot = project([application(3709.78)], [deposit(3770.04, allocation_detail=[
        dict(tipo='Aplicacion historica', aplicacion_id='A', importe_usd=3709.78)])], [credit])
    rows = explain([ledger(3709.78, 'application', 'A'), ledger(-3709.78, 'deposit', 'D')], snapshot)
    assert rows[0]['statement_amount_usd'] == 0
    assert rows[-1]['statement_amount_usd'] == -60.26
    assert rows[-1]['unexplained_usd'] == 0
    assert [(row['source_document'], row['statement_amount_usd'], row['bridge_registration']) for row in changes(rows)] == [
        ('D', -60.26, 'Verificar contrapartida')]


def test_only_used_negative_adjustment_explains_gap_cxc_is_neutral():
    allocations = [dict(tipo='Aplicacion historica', aplicacion_id='A', importe_usd=100),
        dict(tipo='Partida complementaria', partida='ADJ', empresa='E', importe_usd=-10)]
    core = [ledger(100, 'application', 'A'), ledger(-90, 'deposit', 'D')]
    for effect, expected in [('', -10), ('CxC a la empresa', 0)]:
        snapshot = project([application()], [deposit(90, allocation_detail=allocations)],
            [item('ADJ', subcategory_effect=effect), item('UNUSED')])
        rows = explain(core, snapshot)
        assert rows[-1]['unexplained_usd'] == 0
        assert rows[-1]['statement_amount_usd'] == 10 + expected
        assert sum(row['statement_amount_usd'] for row in changes(rows)) == expected
        if expected:
            assert changes(rows)[0]['source_document'] == 'ADJ'
            assert changes(rows)[0]['related_deposits'] == 'D'
            assert changes(rows)[0]['bridge_registration'] == 'Pendiente de registro'


def test_conversion_rounding_requires_matching_original_currency_and_rate():
    snapshot = project([], [deposit(1, allocation_detail=[], deposit_currency='NIO', deposit_amount=36.62, fx_rate=36.62)])
    core = [ledger(-1.02, 'deposit', 'D', core_balance_nio=-36.62, source_currency='NIO', fx_rate=36.62)]
    rows = explain(core, snapshot)
    assert changes(rows)[0]['statement_amount_usd'] == .02
    assert changes(rows)[0]['bridge_registration'] == 'No requiere asiento'
    assert rows[-1]['unexplained_usd'] == 0
    core[0]['core_balance_nio'] = -36.60
    assert changes(explain(core, snapshot))[0]['bridge_registration'] == 'Verificar contrapartida'


def test_compensation_and_credit_application_are_neutral_at_their_effective_dates():
    history = [dict(operation_id='APPLY', application_id='A', fecha='2025-05-15', importe_usd=14.17)]
    credit = item('CREDIT', category='Saldo a favor del cliente', result='Saldo a favor documentado',
        amount_usd=32.63, registered_deposit='D', credit_resolved_usd=14.17, credit_history=history, compensated_usd=0)
    counterpart = item('CORE', category='Compensación entre partidas', amount_usd=18.46,
        accounting_source_key='K', source_debit=18.46, posting_date='2025-06-01', compensated_usd=0)
    offsets = [dict(parent=parent, counterpart=other, compensation_date=date, amount_usd=value)
        for parent, other in [('CREDIT', 'CORE'), ('CORE', 'CREDIT')]
        for date, value in [('2025-06-01', 18.46), ('2025-07-01', -18.46)]]
    core = [ledger(14.17, 'application', 'A'), ledger(-32.63, 'deposit', 'D')]
    for day, expected in [('2025-05-14', -18.46), ('2025-05-15', -18.46), ('2025-06-01', 0), ('2025-07-01', 0)]:
        snapshot = project([application(14.17)], [deposit(32.63, allocation_detail=[])],
            [credit, counterpart], offsets, day)
        dated_core = core + ([ledger(18.46, 'item', 'CORE')] if day >= '2025-06-01' else [])
        rows = explain(dated_core, snapshot)
        assert changes(rows) == []
        assert rows[-1]['statement_amount_usd'] == expected
        assert rows[-1]['unexplained_usd'] == 0


def test_documentary_link_does_not_consume_refund_twice():
    credit = item('CREDIT', category='Saldo a favor del cliente', result='Saldo a favor documentado',
        amount_usd=19.25, registered_deposit='D', credit_resolved_usd=19.25, compensated_usd=19.25,
        credit_history=[dict(operation_id='REFUND', tratamiento='Devolución', fecha='2025-05-01', importe_usd=19.25)])
    other = item('CORE', category='Compensación entre partidas', amount_usd=19.25,
        accounting_source_key='K', source_debit=19.25, posting_date='2025-06-01', compensated_usd=19.25)
    offsets = [dict(parent=parent, counterpart=counterpart, compensation_date='2025-06-01', amount_usd=19.25,
        credit_effect='Vínculo de gestión', credit_management_item='CREDIT', credit_management_entry='REFUND')
        for parent, counterpart in [('CREDIT', 'CORE'), ('CORE', 'CREDIT')]]
    for day, difference in [('2025-04-30', 0), ('2025-05-01', 19.25), ('2025-06-01', 0)]:
        snapshot = project([], [deposit(19.25, allocation_detail=[])], [credit, other], offsets, day)
        core = [ledger(-19.25, 'deposit', 'D')] + ([ledger(19.25, 'item', 'CORE')] if day >= '2025-06-01' else [])
        rows = explain(core, snapshot)
        assert sum(row['statement_amount_usd'] for row in changes(rows)) == difference
        assert rows[-1]['unexplained_usd'] == 0


def test_unattributed_difference_and_missing_conversion_are_never_balancing_entries():
    snapshot = project()
    snapshot['detail'] = [dict(employer='E', balance_usd=12.34)]
    rows = explain([], snapshot)
    assert rows[-2]['bridge_row_type'] == 'unexplained'
    assert rows[-2]['statement_amount_usd'] == 12.34
    assert rows[-2]['bridge_registration'] == 'Revisar'
    assert rows[-1]['bridge_registration'] == 'Revisión pendiente'
    snapshot['detail'][0]['balance_usd'] = None
    rows = explain([ledger(None, 'application', 'A')], snapshot)
    assert rows[0]['statement_amount_usd'] is None
    assert rows[-1]['unexplained_usd'] is None
    assert rows[-1]['statement_amount_usd'] is None


def test_company_boundaries_do_not_hide_an_unexplained_transfer():
    snapshot = project()
    snapshot['detail'] = [dict(employer='E', balance_usd=10), dict(employer='OTHER', balance_usd=-10)]
    rows = bridge.build_bridge([], snapshot)
    assert [(row['employer'], row['unexplained_usd']) for row in rows if row['bridge_row_type'] == 'closing'] == [('E', 10), ('OTHER', -10)]


def test_entry_point_is_dated_and_preserves_report_data_for_export():
    snapshot = project()
    with patch.object(bridge, 'load_core_detail', return_value=[]) as core, \
            patch.object(bridge, 'load_cutoff', return_value=snapshot) as cut, \
            patch.object(bridge, 'cutoff_message', return_value='Corte efectivo 31/05/2025'), \
            patch.object(bridge, 'nowdate', return_value='2025-05-31'):
        columns, rows, message = report.execute(dict(view_mode='Conciliación', employer='E', compact_view=1))
    core.assert_called_once_with(dict(employer='E', cutoff_date='2025-05-31'))
    cut.assert_called_once_with(dict(employer='E', cutoff_date='2025-05-31'), include_bridge=True)
    assert [col['fieldname'] for col in columns if col['fieldtype'] == 'Currency' and not col.get('hidden')] == ['statement_amount_usd']
    assert rows[-1]['bridge_concept'] == 'Saldo de gestión'
    assert 'no representa por sí solo un asiento' in message
    def reject(message):
        raise ValueError(message)

    with patch.object(bridge.frappe, 'throw', side_effect=reject):
        for filters in ({'source_account': '160209'}, {'component': 'client_credit_usd'}, {'accounting_queue': 1}):
            with pytest.raises(ValueError, match='alcance completo'):
                bridge.execute(filters)


def test_summary_does_not_offer_a_misleading_difference_for_partial_ledger():
    assert 'reconciliation_difference_usd' in {col['fieldname'] for col in report.get_columns({'employer': 'E'})}
    for filters in ({'source_account': '160209'}, {'from_date': '2025-05-01'}, {'status': 'Pendiente'}, {'statement_scope': 'Gestión'}):
        assert 'reconciliation_difference_usd' not in {col['fieldname'] for col in report.get_columns(filters)}


def test_bank_date_after_core_posting_is_identified_as_timing_difference():
    snapshot = project([], [deposit(10, deposit_date='2025-06-01', allocation_detail=[])])
    rows = explain([ledger(-10, 'deposit', 'D', event_date='2025-05-31')], snapshot)
    assert changes(rows)[0]['statement_amount_usd'] == 10
    assert changes(rows)[0]['bridge_registration'] == 'Verificar fecha / estado'
    assert rows[-1]['unexplained_usd'] == 0


def test_refund_reversal_stops_explaining_the_gap_from_its_effective_date():
    credit = item(category='Saldo a favor del cliente', result='Saldo a favor documentado',
        amount_usd=20, registered_deposit='D', credit_resolved_usd=0, credit_history=[
            dict(operation_id='REFUND', fecha='2025-05-01', importe_usd=20),
            dict(operation_id='REV', reverses='REFUND', fecha='2025-06-01', importe_usd=-20)])
    for day, difference in [('2025-04-30', 0), ('2025-05-01', 20), ('2025-06-01', 0)]:
        snapshot = project([], [deposit(20, allocation_detail=[])], [credit], cutoff=day)
        rows = explain([ledger(-20, 'deposit', 'D')], snapshot)
        assert sum(row['statement_amount_usd'] for row in changes(rows)) == difference
        assert rows[-1]['unexplained_usd'] == 0


def test_registered_adjustment_does_not_remain_a_pending_bridge_difference():
    adjustment = item('ADJ', accounting_status='Registrada')
    evidence = item('REGISTERED', category='Por clasificar', amount_usd=10, source_credit=10,
        accounting_source_key='K', registration_exception='EX', _registration_origin='ADJ',
        accounting_status='Importada del core')
    snapshot = project([application()], [deposit(90, allocation_detail=[
        dict(tipo='Aplicacion historica', aplicacion_id='A', importe_usd=100),
        dict(tipo='Partida complementaria', partida='ADJ', empresa='E', importe_usd=-10)])], [adjustment, evidence])
    rows = explain([ledger(100, 'application', 'A'), ledger(-90, 'deposit', 'D'), ledger(-10, 'item', 'REGISTERED')], snapshot)
    assert changes(rows) == []
    assert rows[-1]['unexplained_usd'] == 0
