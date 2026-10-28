from unittest.mock import patch

from credinomina_reconciliation import deposit_difference_report as report
from credinomina_reconciliation.company_accounting_statement import summarize
from credinomina_reconciliation.company_statement import build_detail
from credinomina_reconciliation import company_statement_bridge as bridge
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as statement_report


def fixtures():
    deposit = dict(name='DEP', employer='ACEREAL', docstatus=1, deposit_date='2025-06-30',
                   deposit_reference='69812201', cash_identity_status='')
    detail = dict(name='DIFF', parent='DEP', row_kind='Diferencia documentada',
                  difference_amount_usd=-9.32, difference_core_pending_usd=9.32,
                  difference_core_item='', difference_reason='Diferencia de importe',
                  match_status='Diferencia documentada')
    return deposit, detail


def test_one_company_control_does_not_add_to_financial_balance():
    deposit, detail = fixtures()
    row = report.project([deposit], [detail])[0]
    assert row['source_document'] == 'DEP'
    assert row['status'] == 'Pendiente de registro en core'
    assert row['pending_core_difference_usd'] == -9.32
    assert row['management_amount_usd'] == 0
    summary = summarize([row])[0]
    assert summary['pending_core_difference_usd'] == -9.32
    assert summary['management_net_usd'] == 0
    assert summary['accounting_pending_count'] == 1


def test_linked_settled_and_duplicate_origin_do_not_create_a_pending_entry():
    deposit, detail = fixtures()
    detail['difference_core_item'] = 'CORE'
    detail['difference_core_pending_usd'] = 0
    assert report.project([deposit], [detail]) == []
    detail['difference_core_pending_usd'] = 9.32
    linked = report.project([deposit], [detail])[0]
    assert linked['status'] == 'Core vinculado; pendiente de conciliación'
    assert linked['pending_core_difference_usd'] == 0
    assert not linked['accounting_pending']
    detail.update(difference_core_item='', difference_core_pending_usd=9.32)
    deposit['cash_identity_status'] = 'Origen vinculado'
    assert report.project([deposit], [detail]) == []


def test_historical_link_status_is_not_invented():
    deposit, detail = fixtures()
    with patch.object(report, 'nowdate', return_value='2026-10-27'):
        row = report.project([deposit], [detail], cutoff_date='2025-07-01')[0]
    assert row['status'] == 'Estado no determinable al corte'
    assert row['pending_core_difference_usd'] is None
    summary = summarize([row])[0]
    assert summary['pending_core_difference_usd'] is None
    assert summary['management_net_usd'] == 0


def test_invalid_documentation_is_not_reported_as_a_known_pending_amount():
    deposit, detail = fixtures()
    detail.update(match_status='Revisar', match_reason='La diferencia cambió')
    row = report.project([deposit], [detail])[0]
    assert row['status'] == 'Revisar documentación'
    assert row['pending_core_difference_usd'] is None
    assert not row['accounting_pending']


def test_duplicated_company_rows_produce_one_unknown_review_item():
    deposit, detail = fixtures()
    rows = report.project([deposit], [detail, dict(detail, name='OTHER')])
    assert len(rows) == 1
    assert rows[0]['status'] == 'Revisar documentación'
    assert rows[0]['pending_core_difference_usd'] is None


def test_company_report_shows_control_once_without_changing_client_pending():
    deposit, detail = fixtures()
    control = report.project([deposit], [detail])
    applications = [dict(employer='ACEREAL', source_import='IMP', source_rows='A',
                         application_date='2025-06-30', amount_usd=9.32)]
    management = build_detail(applications, [], [], [])
    with patch.object(statement_report, 'load_core_detail', return_value=[]), \
            patch.object(statement_report, 'load_detail', return_value=management), \
            patch.object(statement_report, 'load_deposit_differences', return_value=control), \
            patch('credinomina_reconciliation.report_records.records', return_value=[
                dict(name='Diferencia de importe', treatment='A Favor Empresa')]):
        summary = statement_report.execute({'employer': 'ACEREAL'})[1][0]
        rows = statement_report.execute({'view_mode': 'Detalle', 'employer': 'ACEREAL'})[1]
    assert summary['management_net_usd'] == 9.32
    assert summary['pending_core_difference_usd'] == -9.32
    assert len([row for row in rows if row.get('position_type') == 'Diferencia de depósito']) == 1
    assert len([row for row in rows if row.get('position_type') == 'Aplicación']) == 1
    assert rows[-1]['statement_amount_usd'] == 9.32


def test_bridge_control_never_changes_the_running_balance():
    deposit, detail = fixtures()
    control = report.project([deposit], [detail])
    closing = dict(bridge_row_type='closing', bridge_concept='Saldo de gestión',
                   statement_amount_usd=14.5, bridge_running_usd=14.5)
    with patch.object(bridge, 'load_core_detail', return_value=[]), \
            patch.object(bridge, 'load_cutoff', return_value={}), \
            patch.object(bridge, 'build_bridge', return_value=[closing]), \
            patch.object(bridge, 'cutoff_message', return_value='Corte'), \
            patch.object(bridge, 'load_deposit_differences', return_value=control), \
            patch('credinomina_reconciliation.report_records.records', return_value=[
                dict(name='Diferencia de importe', treatment='A Favor Empresa')]):
        _, rows, _ = bridge.execute({'employer': 'ACEREAL'})
    assert rows[0] == closing
    assert rows[1]['bridge_row_type'] == 'documentary'
    assert rows[1]['pending_core_difference_usd'] == -9.32
    assert rows[1]['statement_amount_usd'] == 0
    assert rows[1]['management_amount_usd'] is None
