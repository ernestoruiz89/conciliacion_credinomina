from copy import deepcopy
from unittest.mock import patch

from credinomina_reconciliation.deposit_detail_classification import prepare_details, expand
from credinomina_reconciliation.company_statement import build_detail
from credinomina_reconciliation.company_accounting_statement import management_detail, summarize, matches
from credinomina_reconciliation.historical_cutoff import reconstruct
from credinomina_reconciliation import difference_reasons

TREATMENTS = {'CLIENTE': 'A Favor Cliente', 'EMPRESA': 'A Favor Empresa'}


def deposit_case():
    pending = [60.26, .01, 3.58, .01, .01]
    amounts = [257.79, 25.42, 28.94, 36.34, 38.39]
    rows = [dict(name=f'R{i}', source_row=i+1, amount_usd=amount, client_name=f'Cliente {i}',
                 loan_number=f'{i+1}-1', difference_reason='CLIENTE') for i, amount in enumerate(amounts)]
    entries = [dict(tipo='Aplicacion historica', aplicacion_id=f'A{i}', fila_detalle=f'R{i}',
                    importe_usd=round(amount-pending[i], 2)) for i, amount in enumerate(amounts)]
    rows.append(dict(name='REST', amount_usd=3383.17))
    entries.append(dict(tipo='Aplicacion historica', aplicacion_id='REST', fila_detalle='REST', importe_usd=3383.17))
    rows.append(dict(name='DIFF', row_kind='Diferencia documentada', difference_amount_usd=-.01,
                     amount_usd=-.01, difference_reason='INMATERIAL'))
    return dict(name='DEP', docstatus=1, employer='E', deposit_date='2026-05-15', amount_usd=3770.04,
                allocated_usd=3706.18, justified_surplus_usd=0, allocation_detail=entries, _statement_detail_rows=rows)


def classify(deposit):
    return expand(management_detail(build_detail([], [], [], [deposit])), TREATMENTS)


def test_actual_five_row_difference_keeps_complete_client_amounts_and_original_signed_balance():
    deposit = deposit_case()
    original = deepcopy(deposit)
    deposit['_deposit_detail_classification'] = prepare_details(deposit)
    rows = classify(deposit)
    assert len(rows) == 6
    assert [row['client_credit_usd'] for row in rows[:5]] == [-60.26, -.01, -3.58, -.01, -.01]
    assert rows[-1]['management_amount_usd'] == .01
    assert rows[-1]['_deposit_classification_residual']
    assert all(row['company_receivable_usd'] == row['employee_receivable_usd'] == 0 for row in rows)
    total = summarize(rows)[0]
    assert total['client_credit_usd'] == -63.87
    assert total['deposit_pending_usd'] == .01
    assert total['management_net_usd'] == -63.86
    assert matches(rows[-1], {'component': 'management_pending_usd'})
    assert all(matches(row, {'component': 'client_credit_usd'}) for row in rows[:5])
    assert {field: value for field, value in deposit.items() if field != '_deposit_detail_classification'} == original


def test_child_treatments_are_loaded_even_when_the_deposit_header_has_no_reason():
    deposit = deposit_case()
    deposit['_deposit_detail_classification'] = prepare_details(deposit)
    rows = management_detail(build_detail([], [], [], [deposit]))
    with patch('credinomina_reconciliation.report_records.records', return_value=[dict(name=k, treatment=v) for k,v in TREATMENTS.items()]) as query:
        difference_reasons.attach_treatments(rows, classify_credits=True)
    query.assert_called_once()
    assert rows[0]['difference_treatment'] == 'A Favor Cliente'
    assert summarize(rows)[0]['client_credit_usd'] == -63.87


def test_no_arbitrary_shortage_without_explicit_matching_company_difference():
    deposit = deposit_case()
    deposit['_statement_detail_rows'].pop()
    deposit['_deposit_detail_classification'] = prepare_details(deposit)
    rows = classify(deposit)
    assert len(rows) == 1 and rows[0]['deposit_pending_usd'] == -63.86
    assert rows[0]['client_credit_usd'] == 0
    assert 'sin una diferencia de empresa' in rows[0]['observation']


def test_partial_company_classification_keeps_the_unclassified_cash_remainder():
    deposit = deposit_case()
    for row in deposit['_statement_detail_rows']:
        row['difference_reason'] = 'EMPRESA' if row['name'] == 'R0' else ''
    deposit['_deposit_detail_classification'] = prepare_details(deposit)
    rows = classify(deposit)
    assert rows[0]['company_credit_usd'] == -60.26
    assert rows[1]['deposit_pending_usd'] == -3.60
    assert summarize(rows)[0]['management_net_usd'] == -63.86


def test_documented_and_refunded_credit_reservations_cannot_be_classified_twice():
    deposit = dict(name='DEP', docstatus=1, employer='E', deposit_date='2025-05-01', amount_usd=10,
        allocated_usd=8, justified_surplus_usd=2, allocation_detail=[dict(importe_usd=8, fila_detalle='R')],
        _statement_detail_rows=[dict(name='R', amount_usd=10, client_credit_usd=2, difference_reason='CLIENTE')])
    data = prepare_details(deposit)
    assert data['rows'] == []
    # Historical reservations use their dated original amount, not the amount
    # still due after a later refund. Today's cached detail credit is ignored.
    deposit['_statement_detail_rows'][0]['client_credit_usd'] = 0
    assert prepare_details(deposit, deposit['allocation_detail'],
        [dict(credit_detail_row='R', amount_usd=2, credit_pending_usd=0)])['rows'] == []


def test_unmapped_or_stale_cash_never_produces_client_credits():
    deposit = deposit_case()
    deposit['allocation_detail'][0].pop('fila_detalle')
    assert prepare_details(deposit) == {}
    deposit = deposit_case()
    deposit['allocated_usd'] = 0
    assert prepare_details(deposit) == {}


def test_cutoff_uses_effective_allocations_instead_of_current_pending_fields():
    context = ([dict(name='A', parent='I', event_type='Aplicacion', effective=1, currency='USD', amount=8,
                    event_date='2025-06-01', historical_period='H', match_status='Conciliado', client_number='1')],
        {'I':dict(name='I', employer='E')}, {'H':dict(name='H', employer='E', payroll_month='2025-06-01', reconciliation_mode='Historica')}, {}, {'E':dict(grace_days=10)})
    deposit = dict(name='DEP', docstatus=1, employer='E', amount_usd=10, deposit_date='2025-05-01',
        allocation_detail=[dict(tipo='Aplicacion historica', aplicacion_id='A', periodo='H', importe_usd=8, fila_detalle='R')],
        _statement_detail_rows=[dict(name='R', amount_usd=10, pending_usd=2, difference_reason='CLIENTE')])
    original = deepcopy(deposit)
    early = reconstruct(context, [deposit], [], [], '2025-05-15')
    later = reconstruct(context, [deposit], [], [], '2025-06-01')
    assert expand(management_detail(early['detail']), TREATMENTS)[0]['client_credit_usd'] == -10
    assert expand(management_detail(later['detail']), TREATMENTS)[0]['client_credit_usd'] == -2
    assert deposit == original
