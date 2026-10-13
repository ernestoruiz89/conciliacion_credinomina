"""Regression cases from the financial queue and report consistency review."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

import pytest

from credinomina_reconciliation.client_position import build_position, summary
from credinomina_reconciliation.company_statement import build_detail, summarize
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.follow_up_queue import build_follow_up, group_work_cases
from credinomina_reconciliation.historical_cutoff import load_cutoff
from credinomina_reconciliation.accounting_control import build_rows
from credinomina_reconciliation.company_transactions import deposit_state
from credinomina_reconciliation.deposit_presentation import reconciliation_status
from credinomina_reconciliation.conciliacion_credinomina.page.control_credinomina.control_credinomina import _build_work_items


def period(mode='Operativa', **changes):
    return dict(dict(name='P', employer='E', employer_name='Empresa', month='2025-04',
        reconciliation_mode=mode, applied_total_usd=100, remitted_total_usd=20, pending_usd=80,
        rows=[], historical_rows=[], exceptions=[], worker_gap_usd=0), **changes)


@pytest.mark.parametrize('mode', ['Historica', 'Operativa'])
def test_financial_queue_covers_both_modes_and_keeps_quality_independent(mode):
    item = period(mode, financial_pending_usd=80, worker_gap_usd=30, remittance_due_date='2025-05-10')
    before = deepcopy(item)
    tasks = _build_work_items([item], [], [], [], [], [], {'E': 'Empresa'}, as_of=date(2025, 5, 11))
    financial = next(row for row in tasks if row['kind'] == 'application_cash')
    assert (financial['amount_usd'], financial['priority'], financial['due_date']) == (80, 0, '2025-05-10')
    assert financial['amount_kind'] == 'Pendiente de cubrir'
    assert financial['target_name'] == 'P'
    assert len([row for row in tasks if row['kind'] == 'application_cash']) == 1
    if mode == 'Operativa':
        quality = next(row for row in tasks if row['kind'] == 'employee_shortfall')
        assert quality['amount_usd'] == 30
        case = group_work_cases(tasks)[0]
        assert case['amount_usd'] == 80  # The quality difference is not added to CxC.
        assert case['action_count'] == 2
    assert item == before


def test_covered_period_has_no_financial_task_even_with_quality_difference():
    item = period(pending_usd=0, remitted_total_usd=100, worker_gap_usd=20)
    tasks = _build_work_items([item], [], [], [], [], [], {})
    assert [row['kind'] for row in tasks] == ['employee_shortfall']


def test_complementary_kpi_excludes_inconsistency_from_valid_pending_total():
    items = [dict(name='X', employer='E', category='Otros ingresos', amount_usd=100, docstatus=1),
             dict(name='Y', employer='E', category='Otros ingresos', amount_usd=50, docstatus=1)]
    balances = {'X': financial_balance(items[0], [dict(amount_usd=110)]), 'Y': financial_balance(items[1])}
    rows = build_position([], [], items, balances, [], {})
    values = {row['label']: row['value'] for row in summary(rows)}
    assert values['Complementarias pendientes de conciliar US$'] == 50
    assert values['Partidas con inconsistencias'] == 1
    assert values['Importes observados US$'] == 10
    assert rows[0]['complementary_pending_usd'] == -10  # Keep the diagnostic evidence.


@pytest.mark.parametrize('category', ['Saldo a favor del cliente', 'Saldo a favor de la empresa'])
def test_resolved_credit_with_conflicting_evidence_is_visible_without_inventing_debt(category):
    item = dict(name='C', employer='E', docstatus=1, category=category, amount_usd=20,
        result='Saldo a favor documentado', credit_management_status='Resuelto', credit_pending_usd=0,
        credit_resolved_usd=20, accounting_source_key='K', source_currency='USD', source_debit=30)
    original = deepcopy(item)
    rows = build_detail([], [], [item], [])
    assert len(rows) == 1
    assert rows[0]['status'] == 'Revisar distribución'
    assert rows[0]['balance_usd'] == 0
    assert rows[0]['observed_usd'] == 30
    assert 'evidencia contable' in rows[0]['observation']
    assert summarize(rows)[0]['balance_usd'] == 0
    assert summarize(rows)[0]['pending_count'] == 1
    assert item == original
    item['source_debit'] = 20
    assert build_detail([], [], [item], []) == []


def test_receivable_management_filter_agrees_between_statements():
    item = dict(name='C', employer='E', category='Ajuste de conciliación', subcategory='Faltante',
        receivable_usd=20, receivable_status='Pendiente de cobro', posting_date='2025-04-30')
    filters = dict(employer='E', management_status='Pendiente de cobro', subcategory='Faltante')
    operational = build_position([], [], [], {}, [], filters, adjustment_receivables=[item])
    company = build_detail([], [], [], [], filters, adjustment_receivables=[item])
    assert len(operational) == len(company) == 1
    assert operational[0]['company_receivable_usd'] == company[0]['company_receivable_usd'] == 20


def test_cutoff_subcategory_filter_uses_loaded_classification():
    item = dict(name='C', employer='E', category='Por clasificar', subcategory='Reclasificación',
        docstatus=0, amount_usd=20, accounting_source_key='K', posting_date='2025-04-30',
        source_date='2025-04-30', source_currency='USD', source_debit=20)
    def records(doctype, **kwargs):
        if doctype == 'CN Complementary Item':
            return [{field: item.get(field) for field in kwargs['fields']}]
        return []
    with patch('frappe.has_permission', return_value=True), patch(
            'credinomina_reconciliation.historical_cutoff.records', side_effect=records), patch(
            'credinomina_reconciliation.historical_cutoff.child_records', return_value=[]), patch(
            'credinomina_reconciliation.historical_cutoff.load_application_context', return_value=([], {}, {}, {}, {})):
        cut = load_cutoff(dict(cutoff_date='2025-04-30', subcategory='Reclasificación'))
    assert len(cut['detail']) == 1
    assert cut['detail'][0]['subcategory'] == 'Reclasificación'
    assert cut['summary'][0]['core_pending_usd'] == 20


def test_registration_and_management_amounts_explain_different_obligations():
    item = dict(name='C', employer='E', docstatus=1, category='Saldo a favor del cliente',
        amount_usd=20, result='Saldo a favor documentado', credit_pending_usd=15,
        credit_management_status='Parcialmente resuelto', accounting_status='Pendiente de registro')
    tasks = build_follow_up([item], {'C': financial_balance(item)}, [])
    by_kind = {row['kind']: row for row in tasks}
    assert by_kind['accounting_registration']['amount_kind'] == 'Importe por registrar/verificar'
    assert by_kind['accounting_registration']['amount_usd'] == 20
    assert by_kind['credit_management']['amount_kind'] == 'Pendiente de devolver/aplicar'
    assert by_kind['credit_management']['amount_usd'] == 15
    assert group_work_cases(tasks)[0]['amount_usd'] == 15


@pytest.mark.parametrize('recorded', ['Parcial con saldo a favor', 'Saldo a favor documentado',
                                     'Conciliado con saldo a favor del cliente', 'Conciliado con saldo a favor'])
def test_complete_deposit_distribution_has_one_financial_state_and_keeps_raw_audit(recorded):
    deposit = dict(name='D', employer='E', docstatus=1, result=recorded, amount_usd=100,
        allocated_usd=80, justified_surplus_usd=20, unclassified_usd=0,
        source_date='2025-04-30', source_account='160209013004', source_currency='USD', source_debit=0,
        source_credit=100, accounting_source_key='K')
    before = deepcopy(deposit)
    rows, _ = build_rows([], {}, [], deposits=[deposit])
    assert rows[0]['state'] == reconciliation_status(deposit) == 'Conciliado con saldo a favor'
    assert rows[0]['recorded_deposit_result'] == recorded
    assert rows[0]['credit_usd'] == 100
    assert deposit_state(deposit) == 'Conciliado'
    assert deposit == before
    deposit['detail_status'] = 'Revisar filas'
    assert reconciliation_status(deposit) == 'Por revisar'
    assert deposit_state(deposit) != 'Conciliado'


def test_deposit_pending_statement_does_not_present_stale_settlement_as_complete():
    deposit = dict(name='D', employer='E', docstatus=1, result='Conciliado', amount_usd=100,
                   allocated_usd=80, justified_surplus_usd=0, deposit_date='2025-04-30')
    assert build_detail([], [], [], [deposit])[0]['status'] == 'Por revisar'
    assert build_detail([], [], [], [deposit])[0]['deposit_pending_usd'] == -20
