import json
from copy import deepcopy
from unittest.mock import Mock, patch

import frappe
import pytest

from credinomina_reconciliation.reconciliation_deposit_detail import build, load
from credinomina_reconciliation.reconciliation_report import build_views


def fixture(**changes):
    periods = [dict(name='P', employer='E', payroll_month='2026-08-01', reconciliation_mode='Historica', applied_usd=20)]
    app = dict(name='A', historical_period='P', event_type='Aplicacion', client_number='1', client_name='Juan',
        loan_number='100-1', currency='USD', amount=20, historical_remitted_usd=20, historical_detail='[{"importe_usd":20}]')
    row = dict(name='R', parent='D', source_row=2, employer='E', client_number='1', client_name='Juan', loan_number='100-1',
               amount_usd=40, client_credit_usd=0, company_credit_usd=0)
    ledger = [dict(tipo='Aplicacion historica', periodo='P', aplicacion_id='A', fila_detalle='R', importe_usd=20)]
    deposit = dict(name='D', employer='E', docstatus=1, amount_usd=40, allocated_usd=20,
        unclassified_usd=20, justified_surplus_usd=0, allocation_detail=json.dumps(ledger))
    deposit.update(changes)
    return periods, app, row, deposit


def test_received_40_assigned_20_exposes_20_without_changing_coverage_or_inputs():
    periods, app, row, deposit = fixture()
    original = deepcopy((periods, app, row, deposit))
    evidence = build(periods, [deposit], [row], [])
    summary, detail = build_views(periods, [], [app], evidence)
    for result in [summary[0], detail[0]]:
        assert (result['application_usd'], result['deposit_usd'], result['open_usd']) == (20, 20, 0)
        assert (result['detail_received_usd'], result['detail_unassigned_usd']) == (40, 20)
        assert result['cash_control'] == 'Recibido sin distribuir'
        assert result['has_difference'] and 'fila 2' in result['observations']
    assert summary[0]['difference_count'] == 1
    assert (periods, app, row, deposit) == original


@pytest.mark.parametrize('kind', ['Aplicacion historica', 'Partida complementaria'])
def test_other_confirmed_destination_consumes_remainder(kind):
    periods, _, row, deposit = fixture(allocated_usd=40, unclassified_usd=0)
    ledger = json.loads(deposit['allocation_detail'])
    ledger.append(dict(tipo=kind, periodo='P', aplicacion_id='OTHER', fila_detalle='R', importe_usd=20))
    deposit['allocation_detail'] = json.dumps(ledger)
    result, = build(periods, [deposit], [row], [])
    assert result['detail_received_usd'] == 40 and result['detail_unassigned_usd'] == 0


def test_documented_credit_is_not_counted_as_undistributed_cash():
    periods, _, row, deposit = fixture(justified_surplus_usd=20, unclassified_usd=0)
    row['client_credit_usd'] = 20
    assert build(periods, [deposit], [row], [])[0]['detail_unassigned_usd'] == 0


def test_company_cent_keeps_detail_and_real_cash_separate_without_false_excess():
    periods, _, row, deposit = fixture(amount_usd=20, allocated_usd=20, unclassified_usd=0)
    row['amount_usd'] = 20.01
    proof = dict(deposit='D', amount_usd=-.01, application_ids=['A'])
    deposit['allocation_detail'] = json.dumps([
        dict(tipo='Aplicacion historica', periodo='P', fila_detalle='R', importe_usd=20.01),
        dict(tipo='Diferencia documentada de empresa', periodo='P', importe_usd=-.01, conversion_empresa=proof)])
    evidence = build(periods, [deposit], [row], [])
    assert evidence[0]['detail_received_usd'] == 20.01 and evidence[0]['detail_unassigned_usd'] == 0


def test_undistributed_cash_never_offsets_another_customers_pending_application():
    periods, app, row, deposit = fixture()
    other = dict(app, name='OTHER', client_number='2', client_name='Pedro', loan_number='200-1',
                 amount=100, historical_remitted_usd=80, historical_detail='[{"importe_usd":80}]')
    periods[0]['applied_usd'] = 120
    summary, _ = build_views(periods, [], [app, other], build(periods, [deposit], [row], []))
    assert summary[0]['open_usd'] == 20 and summary[0]['detail_unassigned_usd'] == 20
    assert summary[0]['cash_control'] == 'Pendiente y recibido sin distribuir'


def test_known_receipt_remainder_does_not_hide_an_unknown_application_amount():
    periods, app, row, deposit = fixture()
    app['currency'] = 'NIO'
    summary, detail = build_views(periods, [], [app], build(periods, [deposit], [row], []))
    assert detail[0]['detail_unassigned_usd'] == 20
    assert detail[0]['open_usd'] is None and detail[0]['cash_control'] == 'Revisar'


def test_future_or_outside_period_destinations_are_consumed_before_filtering():
    periods, _, row, deposit = fixture(amount_usd=80, allocated_usd=60, unclassified_usd=20)
    other = dict(row, name='OTHER', client_number='2', loan_number='200-1')
    ledger = json.loads(deposit['allocation_detail'])
    ledger.append(dict(tipo='Aplicacion historica', periodo='OUTSIDE', fila_detalle='OTHER', importe_usd=40))
    deposit['allocation_detail'] = json.dumps(ledger)
    result, = build(periods, [deposit], [row, other], [])
    assert result['detail_received_usd'] == 40 and result['detail_unassigned_usd'] == 20


def test_multiple_periods_of_one_row_have_unknown_amounts_instead_of_a_split():
    periods, _, row, deposit = fixture()
    periods.append(dict(periods[0], name='P2'))
    deposit['allocation_detail'] = json.dumps([
        dict(tipo='Aplicacion historica', periodo=p, fila_detalle='R', importe_usd=10) for p in ('P', 'P2')])
    evidence = build(periods, [deposit], [row], [])
    assert len(evidence) == 2
    assert all(r['detail_received_usd'] is None and r['detail_unassigned_usd'] is None for r in evidence)


@pytest.mark.parametrize('change', [dict(allocation_detail='broken'), dict(unclassified_usd=0), dict(amount_usd=20)])
def test_inconsistent_bank_pool_does_not_invent_a_customer_excess(change):
    periods, _, row, deposit = fixture(**change)
    evidence = build(periods, [deposit], [row], [])
    if not evidence:  # Without readable links an explicit unique period is needed.
        evidence = build(periods, [deposit], [row], [dict(parent='D', period='P')])
    assert evidence[0]['detail_unassigned_usd'] is None


def test_legacy_ledger_without_detail_row_id_is_disclosed_instead_of_hidden():
    periods, _, row, deposit = fixture()
    deposit['allocation_detail'] = '[{"tipo":"Aplicacion historica","periodo":"P","importe_usd":20}]'
    result, = build(periods, [deposit], [row], [])
    assert result['detail_unassigned_usd'] is None and 'revise' in result['observation']


def test_drafts_cancelled_and_linked_core_origins_do_not_duplicate_receipts():
    for changes in (dict(docstatus=0), dict(docstatus=2), dict(consolidated_deposit='MANUAL')):
        periods, _, row, deposit = fixture(**changes)
        assert build(periods, [deposit], [row], []) == []


def test_missing_bank_permission_yields_unknown_not_zero():
    periods, *_ = fixture()
    with patch.object(frappe, 'has_permission', return_value=False):
        evidence = load(periods)
    assert evidence[0]['detail_unassigned_usd'] is None


def test_loader_reads_complete_details_before_selecting_period_and_never_writes():
    periods, _, row, deposit = fixture()
    def children(doctype, parents, *args, **kw):
        assert set(parents) == {'D'}
        return [row] if doctype == 'CN Remittance Detail' else []
    with patch.object(frappe, 'has_permission', return_value=True), \
            patch('credinomina_reconciliation.remittance_periods.deposit_names_for_periods', return_value=[]), \
            patch('credinomina_reconciliation.reconciliation_deposit_detail.records', return_value=[deposit]), \
            patch('credinomina_reconciliation.reconciliation_deposit_detail.require_complete_evidence') as complete, \
            patch('credinomina_reconciliation.reconciliation_deposit_detail.child_records', side_effect=children), \
            patch.object(frappe, 'db', Mock()) as db:
        assert load(periods)[0]['detail_unassigned_usd'] == 20
        assert complete.call_count == 1
        db.set_value.assert_not_called()
