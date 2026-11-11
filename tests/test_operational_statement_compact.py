from copy import deepcopy
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation import client_position, unallocated_deposit_position
from credinomina_reconciliation.client_position import FINANCIAL_VIEW, FINANCIAL_TYPES
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report


def test_default_columns_show_financial_comparison_and_expand_without_removing_fields():
    compact = report.get_columns({'detailed_columns': '0'})
    full = report.get_columns({'detailed_columns': '1'})
    assert [column['fieldname'] for column in compact if not column.get('hidden')] == [
        'event_date', 'position_type', 'employer', 'client_name', 'loan_number', 'source_document',
        'applied_usd', 'remitted_usd', 'applied_pending_usd', 'deposit_pending_usd',
        'complementary_pending_usd', 'operational_status',
    ]
    assert report.get_columns() == compact
    assert [column['fieldname'] for column in compact] == [column['fieldname'] for column in full]
    assert [column['fieldname'] for column in full if column.get('hidden')] == ['source_doctype']
    assert {'source_rows', 'observation', 'complementary_usd', 'complementary_used_usd',
            'deposit_detail_pending_usd', 'credit_pending_usd', 'difference_reason'} <= {
                column['fieldname'] for column in full if not column.get('hidden')}


@pytest.mark.parametrize('kind, amount', [('Cobranza', 'expected_usd'), ('CxC por ajuste', 'company_receivable_usd')])
def test_control_types_remain_accessible_with_their_own_values(kind, amount):
    columns = report.get_columns({'position_type': kind})
    assert amount in {column['fieldname'] for column in columns if not column.get('hidden')}
    all_columns = report.get_columns({'position_type': '', 'detailed_columns': 1})
    assert amount in {column['fieldname'] for column in all_columns if not column.get('hidden')}


def test_compact_report_loads_three_financial_types_and_expansion_keeps_amounts_totals_and_filters():
    source = dict(name='A', parent='I', event_type='Aplicacion', effective=1, currency='USD', amount=100,
                  event_date='2026-09-30', match_status='Conciliado', historical_period='P',
                  historical_remitted_usd=60, application_adjustment_usd=10, client='7', loan_number='123-1')
    context = [source], {'I': {'employer': 'E'}}, {'P': {'name': 'P', 'employer': 'E'}}, {}, {'E': {}}
    item = dict(name='C', employer='E', docstatus=1, category='Ajuste de conciliación',
                review_status='Lista para conciliar', amount_usd=20, client_number='7', loan_number='123-1',
                posting_date='2026-09-30', accounting_status='Importada del core')
    deposit = dict(position_type='Depósito con saldo pendiente', event_date='2026-09-30',
                   source_doctype='CN Remittance Allocation', source_document='D', employer='E',
                   client_number='7', client_name='ANA PEREZ', loan_number='123-1',
                   deposit_pending_usd=10, deposit_detail_pending_usd=10, source_rows='DR',
                   operational_status='Parcial', is_open=True, usd_currency='USD')
    original = deepcopy((context, item, deposit))

    def records(doctype, **kwargs):
        assert kwargs['filters'].get('employer') == 'E'
        if doctype == 'CN Complementary Item':
            return [item]
        assert doctype == 'CN Client'
        return [dict(name='7', client_number='7', employer='E', client_name='ANA PEREZ')]

    with patch.dict(frappe.session, user='Administrator'), \
         patch.object(client_position, 'load_application_context', return_value=context), \
         patch.object(client_position, 'records', side_effect=records), \
         patch.object(client_position, 'load_balances', return_value={'C': financial_balance(item)}), \
         patch.object(unallocated_deposit_position, 'load', return_value=[deposit]), \
         patch.object(report, 'get_periods', side_effect=AssertionError('Loaded payroll control in financial view')), \
         patch('credinomina_reconciliation.deposit_adjustment_receivables.load_receivables',
               side_effect=AssertionError('Loaded adjustment receivables in financial view')):
        filters = dict(employer='E', client_number='0007', only_open=1)
        compact = report.execute(filters)
        full = report.execute(dict(filters, detailed_columns=1))
        assert compact[1:] == full[1:]
        rows = compact[1]
        assert {row['position_type'] for row in rows[:-1]} == FINANCIAL_TYPES
        footer = rows[-1]
        assert footer['is_total_row']
        assert (footer['applied_usd'], footer['remitted_usd'], footer['applied_pending_usd']) == (90, 60, 30)
        assert footer['deposit_pending_usd'] == 10
        assert footer['complementary_pending_usd'] == 20
        assert report.execute(dict(filters, client_number='999'))[1] == []
        assert report.execute(dict(filters, from_month='2026-10-01'))[1] == []
    assert (context, item, deposit) == original


def test_group_selects_existing_rows_without_combining_or_recalculating_them():
    applications = [dict(source_import='I', employer='E', applied_usd=100, paid_usd=60, amount_usd=40)]
    collections = [dict(period='P', employer='E', expected_usd=120, operational_status='Aplicación insuficiente')]
    receivables = [dict(name='X', employer='E', receivable_usd=30, receivable_status='Pendiente de cobro')]
    args = dict(adjustment_receivables=receivables)
    all_rows = client_position.build_position(collections, applications, [], {}, [], {}, **args)
    grouped = client_position.build_position(collections, applications, [], {}, [], {'position_type': FINANCIAL_VIEW}, **args)
    assert grouped == [row for row in all_rows if row['position_type'] in FINANCIAL_TYPES]
    _, totals = client_position.summarize_selection(grouped, {'position_type': FINANCIAL_VIEW})
    assert totals == dict(applied_usd=100, remitted_usd=60, applied_pending_usd=40)
