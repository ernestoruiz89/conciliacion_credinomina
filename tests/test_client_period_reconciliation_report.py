from unittest.mock import Mock, patch

import frappe
import pytest

from credinomina_reconciliation.conciliacion_credinomina.report.conciliacion_por_cliente_y_periodo import conciliacion_por_cliente_y_periodo as report
from credinomina_reconciliation.reconciliation_report import build_views


def detail(**changes):
    return dict(client_name='Juan', employer='INDENICSA', loan_number='12919-1', loan_number_display='0012919-1',
        detail_received_usd=46.90, application_usd=39.85, detail_unassigned_usd=7.05, deposit_usd=39.85,
        open_usd=0, has_difference=True, portfolio_credit_status='Cancelado', comments='',
        first_exception_comment='', deposit_difference_notes='') | changes


PERIOD = dict(name='P', employer='INDENICSA', collection_cycle='Segunda quincena')


def test_requested_columns_and_documented_comments_only_for_cancelled_loan():
    row, = report.project([detail()], PERIOD)
    assert (row['bank_usd'], row['applied_usd'], row['pending_usd']) == (46.90, 39.85, 7.05)
    assert row['loan_number'] == '0012919-1' and row['qna'] == 'II QNA'
    assert row['state'] == 'Revisar' and row['credit_status'] == 'Cancelado'
    assert row['comment'] == '' and 'reembolso' not in row['detail'].casefold()
    assert [column['fieldname'] for column in report.get_columns()] == [
        'row_number', 'client_name', 'employer', 'loan_number', 'bank_usd', 'applied_usd', 'pending_usd',
        'state', 'credit_status', 'comment', 'detail', 'qna']


def test_only_saved_comment_and_reason_are_shown_without_refinance_inference():
    source = detail()
    source.update(comments='Comentarios: Posible reembolso', deposit_difference_notes='Cliente solicitó revisión',
                  difference_reason='Refinanciado', observations='DEP-1 · fila 2')
    row, = report.project([source], PERIOD)
    assert 'Posible reembolso' in row['comment']
    assert 'Cliente solicitó revisión' in row['comment']
    assert 'Refinanciado' in row['detail']
    assert 'DEP-1' in row['detail'] and 'DEP-1' not in row['comment']


def test_other_destinations_and_documented_credit_are_not_pending_reimbursements():
    source = detail()
    source.update(detail_unassigned_usd=0, has_difference=False)
    row, = report.project([source], PERIOD)
    assert row['pending_usd'] == 0 and row['state'] == 'Conciliado'
    assert row['bank_usd'] - row['applied_usd'] > 0  # A subtraction would invent another balance.


def test_unknown_or_missing_individual_bank_detail_is_never_fabricated():
    source = detail()
    source.update(detail_received_usd=0)
    row, = report.project([source], PERIOD)
    assert row['bank_usd'] is row['pending_usd'] is None and row['state'] == 'Revisar'
    unavailable = dict(_detail_unavailable=True)
    source.update(detail_received_usd=46.90)
    row, = report.project([source, unavailable], PERIOD)
    assert row['bank_usd'] is row['pending_usd'] is None


def test_period_cycle_is_explicit_never_guessed_from_dates():
    for fields, expected in ((dict(collection_cycle='Primera quincena'), 'I QNA'),
                             (dict(collection_cycle='Mensual'), 'Mensual'),
                             (dict(collection_cycle='', reconciliation_mode='Historica', historical_scope='Fecha exacta'), 'Fecha exacta')):
        row, = report.project([detail()], PERIOD | fields)
        assert row['qna'] == expected


def test_filters_and_numbering_apply_after_search():
    first, second = detail(), detail()
    first['client_name'], second['client_name'] = 'Beatriz', 'Ana'
    first['detail_unassigned_usd'] = 0
    rows = report.project([first, second], PERIOD, dict(only_pending=1, state='Revisar', credit_status='Cancelado'))
    assert len(rows) == 1 and rows[0]['client_name'] == 'Ana' and rows[0]['row_number'] == 1
    assert not report.project([second], PERIOD, dict(search='No existe'))


def test_period_required_and_read_permission_checked_before_loading_financial_data():
    with patch.object(frappe, 'throw', side_effect=ValueError), pytest.raises(ValueError):
        report.execute({})
    document = Mock()
    document.check_permission.side_effect = PermissionError
    with patch.object(frappe, 'get_doc', return_value=document), patch.object(report, 'reconciliation_detail') as source, pytest.raises(PermissionError):
        report.execute(dict(period='PRIVATE'))
    source.assert_not_called()


def test_unknown_values_suppress_totals_and_queries_are_read_only():
    period = Mock(name='period')
    period.name = 'P'
    period.as_dict.return_value = PERIOD
    source = detail()
    source['detail_unassigned_usd'] = None
    with patch.object(frappe, 'get_doc', return_value=period), \
            patch.object(report, 'reconciliation_detail', return_value=([], [source], None)) as read, \
            patch.object(frappe, 'db', Mock()) as db:
        columns, rows, message, chart, totals, skip_total = report.execute(dict(period='P'))
        read.assert_called_once_with(dict(period='P', view='Detalle'))
        assert rows[0]['pending_usd'] is None and totals == [] and 'omitidos' in message
        assert skip_total and chart is None
        db.set_value.assert_not_called()


def test_bank_detail_comments_and_original_credit_are_preserved_in_shared_read_model():
    periods = [dict(name='P', employer='E', reconciliation_mode='Historica', applied_usd=20)]
    apps = [dict(name='A', historical_period='P', event_type='Aplicacion', client_number='1', client_name='Juan',
        loan_number='0012919-1', currency='USD', amount=20, historical_remitted_usd=20,
        historical_detail='[{"importe_usd":20}]')]
    evidence = [dict(period='P', source=dict(name='R', client_number='1', loan_number='12919-1',
        comments='Revisar importe', application_comment='0', difference_reason='Refinanciado', difference_notes='Posible reembolso'),
        detail_received_usd=40, detail_unassigned_usd=20)]
    _, rows = build_views(periods, [], apps, evidence)
    assert rows[0]['loan_number_display'] == '0012919-1'
    assert rows[0]['comments'] == 'Revisar importe' and rows[0]['application_comment'] == ''
    assert rows[0]['deposit_difference_notes'] == 'Posible reembolso'
    assert rows[0]['difference_reason'] == 'Refinanciado'


def test_navigation_patch_adds_once_and_preserves_site_links():
    from credinomina_reconciliation.patches.v1_0 import add_client_period_reconciliation_report as migration
    rows = [frappe._dict(link_type='Report', link_to='Resumen de Conciliacion'),
            frappe._dict(link_type='Page', link_to='site-custom-link')]
    custom = rows[1]
    sidebar = Mock()
    sidebar.get.return_value = rows
    def append(field, values):
        row = frappe._dict(values)
        rows.append(row)
        return row
    sidebar.append.side_effect = append
    with patch.object(frappe, 'reload_doc'), patch.object(frappe, 'get_doc', return_value=sidebar), \
            patch.object(frappe, 'db', Mock(exists=Mock(return_value=True))), patch.object(frappe, 'clear_cache'), \
            patch('credinomina_reconciliation.patches.v1_0.order_workspace_by_workflow.execute'):
        migration.execute()
        migration.execute()
    assert [row.link_to for row in rows] == ['Resumen de Conciliacion', migration.REPORT, 'site-custom-link']
    assert rows[2] is custom
    sidebar.save.assert_called_once_with(ignore_permissions=True)
