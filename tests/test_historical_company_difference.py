"""Company differences at a cutoff preserve cash and every client allocation."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from credinomina_reconciliation import historical_cutoff as cutoff
from credinomina_reconciliation.company_conversion import from_rows, journal_entry
from credinomina_reconciliation.rounding import sum_money


@pytest.fixture
def population():
    # The reported deposit: 17 applications of 490.53, bank cash of 490.52.
    amounts = [31.90, 21.13, 10.77, 20.23, 11.44, 26.45, 10.37, 21.95, 29.48,
               22.10, 21.96, 21.95, 64.62, 24.20, 32.23, 93.13, 26.62]
    sources = [dict(name=f'A{i}', parent='I', event_type='Aplicacion', effective=1,
        currency='USD', amount=amount, event_date='2026-03-15', historical_period='P',
        match_status='Conciliado', client_number=str(i), client_name=f'Cliente {i}',
        historical_remitted_usd=amount) for i, amount in enumerate(amounts)]
    context = (sources, {'I': dict(name='I', employer='E')},
        {'P': dict(name='P', employer='E', payroll_month='2026-03-01', reconciliation_mode='Historica')},
        {}, {'E': dict(grace_days=10)})
    proof = dict(deposit='D', employer='E', period='P', difference_row='DIFF', amount_usd=-.01,
                 difference_reason='Diferencia Inmaterial', application_ids=[row['name'] for row in sources])
    ledger = [journal_entry(proof), *[dict(tipo='Aplicacion historica', periodo='P', empresa='E',
        aplicacion_id=row['name'], importe_usd=row['amount'], fila_detalle=f"R{row['name']}") for row in sources]]
    deposit = dict(name='D', employer='E', docstatus=1, deposit_date='2026-03-13',
        amount_usd=490.52, allocated_usd=490.52, allocation_detail=ledger)
    return context, deposit, proof


def project(population, day='2026-08-31'):
    context, deposit, _proof = population
    return cutoff.reconstruct(context, [deposit], [], [], day, include_bridge=True)


@pytest.mark.parametrize('serialized', [False, True])
def test_company_cent_counts_once_without_a_client_haircut_or_writes(population, serialized):
    context, deposit, proof = population
    if serialized:
        deposit['allocation_detail'] = json.dumps(deposit['allocation_detail'])
    original = deepcopy(population)
    result = project(population)
    assert result['summary'] == []
    assert result['warnings'] == []
    assert len(result['applications']) == 17
    assert all(row['amount_usd'] == 0 for row in result['applications'])
    assert sum_money(row['paid_usd'] for row in result['applications']) == sum_money([490.53])
    projected = result['bridge_context']['deposits'][0]
    assert projected['amount_usd'] == projected['allocated_usd'] == 490.52
    assert from_rows(result['bridge_context']['sources']) == [proof]
    assert result['bridge_context']['distributions'] == {}
    assert population == original


def test_other_deposits_and_existing_partial_coverage_stay_independent(population):
    context, deposit, _proof = population
    context[0].append(dict(context[0][0], name='OTHER', amount=100, historical_remitted_usd=75))
    other_deposits = [dict(name=f'D{amount}', employer='E', docstatus=1, deposit_date='2026-04-01',
        amount_usd=amount, allocated_usd=amount, allocation_detail=[dict(tipo='Aplicacion historica',
            aplicacion_id='OTHER', periodo='P', importe_usd=amount)]) for amount in (25, 50)]
    inputs = (context, [deposit, *other_deposits])
    original = deepcopy(inputs)
    result = cutoff.reconstruct(*inputs, [], [], '2026-08-31', include_bridge=True)
    remaining = next(row for row in result['applications'] if row['source_rows'] == 'OTHER')
    assert (remaining['paid_usd'], remaining['amount_usd']) == (75, 25)
    assert result['summary'][0]['application_pending_usd'] == 25
    assert result['summary'][0]['deposit_pending_usd'] == 0
    assert [row['allocated_usd'] for row in result['bridge_context']['deposits']] == [490.52, 25, 50]
    assert inputs == original


@pytest.mark.parametrize('day, pending', [('2026-03-12', None), ('2026-03-13', -490.52)])
def test_difference_is_not_used_before_deposit_and_application_dates(population, day, pending):
    result = project(population, day)
    assert result['applications'] == []
    assert result['warnings'] == []
    assert (result['summary'][0]['deposit_pending_usd'] if result['summary'] else None) == pending
    assert from_rows(result['bridge_context']['sources']) == []


def test_cut_through_shared_difference_does_not_guess_a_client_split(population):
    context, _deposit, _proof = population
    context[0][-1]['event_date'] = '2026-04-15'
    original = deepcopy(population)
    result = project(population, '2026-03-31')
    assert result['summary'][0]['application_pending_usd'] == 463.91
    assert result['summary'][0]['deposit_pending_usd'] == -490.52
    assert result['bridge_context']['deposits'][0]['allocated_usd'] == 0
    assert result['warnings'] and 'diferencia de empresa' in result['warnings'][0]
    assert from_rows(result['bridge_context']['sources']) == []
    assert project(population)['summary'] == []
    assert population == original


@pytest.mark.parametrize('field,value', [
    ('deposit', 'OTHER'), ('employer', 'OTHER'), ('period', 'OTHER'), ('difference_row', 'OTHER'),
    ('amount_usd', -.02), ('application_ids', []), ('application_ids', ['A0', 'A0']),
    ('application_ids', ['A0']), ('application_ids', [None]), ('application_ids', 'A0'),
])
def test_invalid_company_documentation_cannot_create_cash(population, field, value):
    population[2][field] = value
    with pytest.raises(ValueError, match='diferencia de empresa'):
        project(population)


def test_duplicate_company_line_and_missing_application_are_rejected(population):
    context, deposit, _proof = population
    deposit['allocation_detail'].append(deepcopy(deposit['allocation_detail'][0]))
    with pytest.raises(ValueError, match='diferencia de empresa'):
        project(population)
    deposit['allocation_detail'].pop()
    context[0].pop()
    with pytest.raises(ValueError, match='aplicación vinculada'):
        project(population)


def test_only_known_company_type_is_accepted(population):
    population[1]['allocation_detail'][0]['tipo'] = 'Tipo desconocido'
    with pytest.raises(ValueError, match='tipo de distribución no reconocido'):
        project(population)


def test_report_entry_point_loads_and_projects_current_journal(population):
    from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as report

    context, deposit, _proof = population
    original = deepcopy(population)

    def records(doctype, *, fields, **kwargs):
        return [{field: deposit.get(field) for field in fields}] if doctype == 'CN Remittance Allocation' else []

    with patch.object(cutoff.frappe, 'has_permission', return_value=True), \
            patch.object(cutoff, 'records', side_effect=records), \
            patch.object(cutoff, 'child_records', return_value=[]), \
            patch.object(cutoff, 'load_application_context', return_value=context), \
            patch('credinomina_reconciliation.cutoff_evidence.require_cutoff_evidence'), \
            patch('credinomina_reconciliation.report_records.child_records', return_value=[]), \
            patch.object(report, 'load_deposit_differences', return_value=[]), \
            patch.object(report, 'load_core_detail', return_value=[]), \
            patch.object(cutoff, 'cutoff_message', return_value='Corte 31/08/2026'), \
            patch.object(cutoff.frappe.db, 'set_value') as write:
        for view in ('Resumen', 'Detalle'):
            columns, rows, message = report.execute(dict(employer='E', cutoff_date='2026-08-31', view_mode=view))
            assert rows == []
            assert 'Corte 31/08/2026' in message
        write.assert_not_called()
    assert population == original
