import json
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation import reconciliation_report_cash as cash
from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion import resumen_de_conciliacion as report


def execute(view, payments, *, clients=None, unknown=False, differences=False, signed_offset=-10):
    """A US$ 90 deposit with a signed -10 offset, plus any separate payments."""
    clients = clients or list(map(str, range(len(payments))))
    apps = [dict(name=f'A{i}', historical_period='P', currency='USD', amount=amount,
                 event_type='Aplicacion', client=clients[i], client_name=f'Cliente {clients[i]}',
                 loan_number=f'{i+1}-1', historical_remitted_usd=amount,
                 historical_detail=[dict(importe_usd=amount)]) for i, amount in enumerate(payments)]
    p = frappe._dict(name='P', employer='E', reconciliation_mode='Historica', payroll_month='2026-05-01',
                     applied_usd=sum(payments), negative_complementary_usd=signed_offset, deposit_coverage_usd=sum(payments)-10,
                     cash_breakdown_known=int(not unknown), cash_breakdown_note='Depósito entre varios períodos.' if unknown else '')
    journal = [dict(tipo='Aplicacion historica', periodo='P', aplicacion_id=app['name'], importe_usd=app['amount'])
               for app in (apps if len(apps) == 1 else apps[:2])]
    journal.append(dict(tipo='Partida complementaria', partida='COMP', importe_usd=-10))
    deposits = {'DEP': dict(name='DEP', allocated_usd=90, allocation_detail=json.dumps(journal))}
    with patch.object(report, 'records', return_value=[p]), patch.object(report, 'child_records', return_value=[]), \
         patch.object(report, 'load_direct_applications', return_value=apps), patch.object(report, 'add_portfolio_status'), \
         patch.object(report, 'load_pending_detail', return_value=[]), patch.object(frappe, 'has_permission', return_value=True), \
         patch.object(cash, 'load_period_deposits', return_value=deposits):
        return report.execute(dict(view=view, only_differences=differences))


@pytest.mark.parametrize('view', ['Resumen', 'Detalle', 'Detalle por Cliente'])
def test_single_application_reports_actual_cash_signed_adjustment_and_known_pending(view):
    result = execute(view, [100], differences=True)
    row, = result[1]
    assert (row['deposit_usd'], row['cash_adjustment_usd'], row['open_usd']) == (90, -10, 0)
    assert row['deposit_difference_usd'] == 10 and not result[5]


def test_shared_offset_does_not_change_fully_known_cash_of_an_unrelated_customer():
    result = execute('Detalle', [40, 60, 25])
    rows = result[1]
    assert [r['deposit_usd'] for r in rows] == [None, None, 25]
    assert [r['cash_adjustment_usd'] for r in rows] == [None, None, 0]
    assert all(r['open_usd'] == 0 for r in rows) and result[5]
    row, = execute('Resumen', [40, 60, 25])[1]
    assert (row['deposit_usd'], row['cash_adjustment_usd'], row['difference_count']) == (115, -10, 2)


def test_shared_credits_are_determinable_when_they_belong_to_one_client():
    rows = execute('Detalle por Cliente', [40, 60, 25], clients=['A','A','B'])[1]
    assert [(r['client_number'], r['deposit_usd'], r['open_usd']) for r in rows] == [('A',90,0), ('B',25,0)]


@pytest.mark.parametrize('view', ['Resumen', 'Detalle', 'Detalle por Cliente'])
def test_an_unattributable_period_offset_keeps_the_actual_outstanding_amount(view):
    result = execute(view, [100], unknown=True)
    row, = result[1]
    assert row['deposit_usd'] is None and row['cash_adjustment_usd'] is None and result[5]
    assert row['application_usd'] == 100 and row['open_usd'] == 0 and 'varios períodos' in row['observations']


def test_saved_cash_that_disagrees_with_allocations_cannot_certify_a_zero_offset():
    result = execute('Resumen', [100], signed_offset=0)
    row, = result[1]
    assert result[5] and row['deposit_usd'] is None and row['open_usd'] == 0
