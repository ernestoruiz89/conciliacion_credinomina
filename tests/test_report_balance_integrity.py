"""Invalid evidence cannot create debt/credits or publish a complete total."""
from copy import deepcopy
import io
import json
from datetime import datetime
from unittest.mock import patch

import pytest
from openpyxl import load_workbook

from credinomina_reconciliation.application_aging import application_balances, balance_status
from credinomina_reconciliation.accounting_client_summary import build_summary
from credinomina_reconciliation.company_transactions import application_progress_index
from credinomina_reconciliation.company_statement import build_detail, summarize
from credinomina_reconciliation.client_position import build_position
from credinomina_reconciliation.control_kpis import summarize_applications
from credinomina_reconciliation.control_export import build_control_workbook
from credinomina_reconciliation.deposit_adjustment_receivables import build_receivables, receivable_total
from credinomina_reconciliation.follow_up_queue import build_follow_up
from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos import antiguedad_de_saldos as aging
from credinomina_reconciliation.conciliacion_credinomina.report.antiguedad_de_saldos_por_empresa import antiguedad_de_saldos_por_empresa as grouped
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as statement


def context():
    row = dict(name='R', parent='I', event_type='Aplicacion', effective=1, currency='USD', amount=100,
        event_date='2025-04-15', match_status='Conciliado', client_name='Ana', client_number='1',
        loan_number='10-1', collection_row_id='C')
    imports = {'I':dict(name='I', status='Importado', employer='E')}
    periods = {'P':dict(name='P', employer='E', reconciliation_mode='Operativa', payroll_month='2025-04-01')}
    collections = {'C':dict(name='C', parent='P', applied_usd=100, remittance_detail='[]')}
    return [row], imports, periods, collections, {'E':dict(grace_days=10)}


@pytest.mark.parametrize('links,capacity', [
    ([dict(collection_row_id='C', amount_usd=150)], 150),
    ([dict(collection_row_id='C', amount_usd=-10), dict(collection_row_id='C', amount_usd=110)], 100),
    ([dict(collection_row_id='C', amount_usd=100)], 50),
    ('not-json', 100),
])
def test_inconsistent_application_retains_original_and_unknown_balance_across_reports(links, capacity):
    inputs = context()
    inputs[0][0]['application_allocation_detail'] = json.dumps(links) if not isinstance(links, str) else links
    inputs[3]['C']['applied_usd'] = capacity
    original = deepcopy(inputs)
    row = application_balances(*inputs, '2025-06-01', include_reviews=True)[0]
    assert row['applied_usd'] == 100
    assert row['amount_usd'] is None and row['paid_usd'] is None
    assert row['days_16_30'] is None
    assert balance_status(row) == 'Revisar distribución'
    transaction = application_progress_index(inputs[0], inputs[1], inputs[3])['R']
    assert transaction['state'] == balance_status(row) and transaction['pending_usd'] is None
    detail = build_detail([row], [], [], [])
    assert summarize(detail)[0]['application_pending_usd'] is None
    assert summarize(detail)[0]['balance_usd'] is None
    position = build_position([], [row], [], {}, [], {})[0]
    assert position['applied_pending_usd'] is None and position['is_open']
    kpis = summarize_applications([row])
    assert kpis['net_applied_usd'] == 100 and kpis['pending_usd'] is None
    assert kpis['review_count'] == 1 and kpis['missing_fx_count'] == 0
    assert inputs == original


def test_shared_claim_overlinked_across_imports_is_reviewed_once_per_source():
    inputs = context()
    inputs[0].append(dict(inputs[0][0], name='R2', parent='I2'))
    inputs[1]['I2'] = dict(inputs[1]['I'], name='I2')
    rows = application_balances(*inputs, '2025-06-01')
    assert len(rows) == 2 and sum(row['applied_usd'] for row in rows) == 200
    assert all(row['amount_usd'] is None for row in rows)
    states = application_progress_index(inputs[0], inputs[1], inputs[3])
    assert all(row['state'] == 'Revisar distribución' for row in states.values())
    client = build_summary(dict(rows=inputs[0]), inputs[3])[0]
    assert client['applied_usd'] == 200 and client['balance_usd'] is None


def test_valid_split_and_nio_conversion_still_count_money_once():
    inputs = context()
    inputs[0][0].update(currency='NIO', amount=3662.43, manual_fx_rate=36.6243,
        application_allocation_detail=json.dumps([dict(collection_row_id='C', amount_usd=40),
                                                dict(collection_row_id='C2', amount_usd=60)]))
    inputs[3]['C'].update(applied_usd=40, remittance_detail='[{"importe_usd":30}]')
    inputs[3]['C2'] = dict(name='C2', parent='P', applied_usd=60, remittance_detail='[{"importe_usd":50}]')
    rows = application_balances(*inputs, '2025-06-01')
    assert sum(row['applied_usd'] for row in rows) == 100
    assert sum(row['paid_usd'] for row in rows) == 80
    assert sum(row['amount_usd'] for row in rows) == 20


def receivable(compensated):
    origin = dict(name='ADJ', docstatus=1, category='Ajuste de conciliación',
        subcategory_effect='CxC a la empresa', amount_usd=-10, employer='E', posting_date='2025-04-15')
    receipt = dict(name='REC', receivable_origin='ADJ', docstatus=1,
        category='Compensación entre partidas', employer='E', compensated_usd=compensated)
    return build_receivables([origin], {'ADJ':dict(distributions=[dict(employer='E', amount_usd=-10)])},
        settlements=[receipt], include_settled=True)


@pytest.mark.parametrize('amount', [15, -5])
def test_invalid_recovery_is_not_an_automatic_credit_in_reports_or_follow_up(amount):
    rows = receivable(amount)
    row = rows[0]
    assert row['receivable_usd'] is None and row['receivable_status'] == 'Revisar cobros'
    assert receivable_total(rows) is None
    assert row['receivable_original_usd'] == 10 and row['observed_usd'] == 5
    assert summarize(build_detail([], [], [], [], adjustment_receivables=rows))[0]['balance_usd'] is None
    position = build_position([], [], [], {}, [], {}, adjustment_receivables=rows)[0]
    assert position['company_receivable_usd'] is None
    task = build_follow_up([], {}, [], adjustment_receivables=rows)[0]
    assert task['amount_kind'] == 'Importe observado' and task['amount_usd'] == 5
    assert 'Revisar cobros' in task['summary']


@pytest.mark.parametrize('amount,pending,status', [(0,10,'Pendiente de cobro'), (4,6,'Parcialmente cobrada'), (10,0,'Cobrada')])
def test_valid_receivable_recoveries_remain_exact(amount, pending, status):
    rows = receivable(amount)
    assert rows[0]['receivable_usd'] == pending and rows[0]['receivable_status'] == status
    assert receivable_total(rows) == pending


def test_aging_total_and_grouped_export_distinguish_known_subtotal_from_unknown():
    sources, imports, periods, collections, employers = context()
    sources[0].update(collection_row_id='', match_status='Sin coincidencia')
    sources.append(dict(sources[0], name='FX', currency='NIO', manual_fx_rate=0))
    with patch.object(aging, 'load_application_context', return_value=(sources, imports, periods, collections, employers)):
        detailed = aging.execute(dict(as_of_date='2025-06-01', balance_type=aging.APPLICATION_BALANCE))
    assert detailed[4][0]['value'] == 'N/D'
    assert 'pendiente de determinar' in detailed[4][0]['label']
    assert detailed[4][1]['label'] == 'Subtotal conocido US$' and detailed[4][1]['value'] == 100
    assert detailed[4][-1]['value'] == 1
    assert 'El subtotal conocido no representa la CxC total' in detailed[2]
    with patch.object(grouped.detailed, 'execute', return_value=detailed):
        columns, rows, *_ = grouped.execute()
    assert rows[0]['amount_usd'] is None and rows[0]['known_amount_usd'] == 100
    assert 'known_amount_usd' in {column['fieldname'] for column in columns}
    assert 'incompletos' in rows[0]['observation']


def test_invalid_recovery_excel_summary_is_unknown_and_preserves_observation():
    rows = receivable(15)
    detail = build_detail([], [], [], [], adjustment_receivables=rows)
    payload = build_control_workbook(dict(year=2025, periods=[], adjustment_receivables=rows,
        company_statement=dict(columns=statement.get_columns(), rows=summarize(detail), message='Revisar cobros')),
        exceptions=[], actions=[], employer_label='E', generated_at=datetime(2026,10,12))
    book = load_workbook(io.BytesIO(payload), data_only=True)
    assert book['Resumen']['H5'].value == 'N/D' and book['Resumen']['J5'].value == 'N/D'
    assert book['Resumen']['I5'].value == 0  # Employee debt is a separate component.
    assert book['Partidas y excepciones']['H5'].value == 'N/D'
    assert 'el exceso no es un saldo a favor' in book['Partidas y excepciones']['P5'].value
