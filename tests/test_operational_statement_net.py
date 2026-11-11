from copy import deepcopy
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation import client_position as position
from credinomina_reconciliation.complementary_balances import financial_balance
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo import estado_de_cuenta_operativo as report


FILTERS = dict(position_type=position.FINANCIAL_VIEW)


def figures(rows, filters=None):
    filters = FILTERS if filters is None else filters
    _, amounts = position.summarize_selection(rows, filters)
    return {r['label']: r for r in position.net_balance_summary(rows, amounts, filters)}


def complementary(name='X', amount=20, used=0, **values):
    item = dict(name=name, docstatus=1, category='Ajuste de conciliación', employer='E',
                posting_date='2026-05-01', amount_usd=amount, **values)
    balance = financial_balance(item, [dict(amount_usd=used)] if used else [])
    before = deepcopy((item, balance))
    rows = position.build_position([], [], [item], {name: balance}, [], FILTERS)
    assert (item, balance) == before
    return rows[0]


def test_immsa_net_uses_pending_amounts_and_signed_complementaries_once():
    rows = [dict(position_type='Aplicación', applied_usd=204689.22, applied_pending_usd=13510.48,
                 remitted_usd=191178.74),
            dict(position_type='Depósito con saldo pendiente', source_document='DEP', deposit_pending_usd=7331.70),
            complementary('D', amount=154.27, used=100, source_debit=154.27),
            complementary('C', amount=108.51, used=100, source_credit=108.51)]
    before = deepcopy(rows)
    result = figures(rows)
    assert result['Complementarias pendientes netas US$']['value'] == 45.76
    assert result['Saldo neto de conciliación US$']['value'] == 6224.54
    assert rows == before


@pytest.mark.parametrize('values, expected', [
    ({'source_debit': 20}, 20), ({'source_credit': 20}, -20),
    ({'source_debit': 30, 'source_credit': 10}, 20),
    ({'source_debit': 10, 'source_credit': 30}, -20),
    ({}, None), ({'source_debit': 20, 'source_credit': 20}, None),
])
def test_sign_follows_accounting_evidence_not_category_or_client(values, expected):
    row = complementary(**values)
    assert row['_signed_complementary_pending_usd'] == expected
    result = figures([row])
    if expected is None:
        assert 'Saldo neto de conciliación US$' not in result
        assert any('no determinable' in label for label in result)
    else:
        assert result['Saldo neto de conciliación US$']['value'] == expected


@pytest.mark.parametrize('field, kind', [('applied_pending_usd', 'Aplicación'),
                                      ('deposit_pending_usd', 'Depósito con saldo pendiente')])
def test_unknown_balance_prevents_a_numeric_net(field, kind):
    result = figures([dict(position_type=kind, source_document='DOC', **{field: None})])
    assert 'Saldo neto de conciliación US$' not in result
    assert any('no determinable' in label for label in result)


def test_settled_item_needs_no_sign_and_inconsistent_item_blocks_net():
    row = complementary(used=20)
    assert figures([row])['Saldo neto de conciliación US$']['value'] == 0
    row = complementary(used=21, source_debit=20)
    assert 'Saldo neto de conciliación US$' not in figures([row])


def test_net_uses_bank_remainder_once_when_customer_detail_is_unknown():
    rows = [dict(position_type='Aplicación', applied_pending_usd=30),
            dict(position_type='Depósito con saldo pendiente', source_document='D', deposit_pending_usd=20),
            dict(position_type='Depósito con saldo pendiente', source_document='D', source_rows='R',
                 deposit_pending_usd=None, deposit_detail_pending_usd=50)]
    assert figures(rows)['Saldo neto de conciliación US$']['value'] == 10
    assert 'Saldo neto de conciliación US$' not in figures(rows[::2])


@pytest.mark.parametrize('kind', ['Aplicación', 'Partida complementaria', 'Depósito con saldo pendiente', 'Cobranza', 'CxC por ajuste'])
def test_no_global_net_when_a_financial_type_is_excluded(kind):
    assert figures([], {'position_type': kind}) == {}
    assert figures([], {'position_type': ''})['Saldo neto de conciliación US$']['value'] == 0


def test_report_summary_uses_only_permission_filtered_rows_and_keeps_columns():
    rows = [dict(position_type='Aplicación', applied_usd=100, remitted_usd=0, applied_pending_usd=100),
            dict(position_type='Aplicación', applied_usd=25, remitted_usd=0, applied_pending_usd=25)]
    with patch.dict(frappe.session, user='restricted'), \
            patch.object(report, '_collection_report', return_value=(report.get_columns(), [])), \
            patch.object(report, 'load_position', return_value=deepcopy(rows)), \
            patch('frappe.desk.query_report.get_filtered_data', side_effect=lambda dt, cols, data, user: data[1:]):
        columns, data, message, _, summary = report.execute(FILTERS)
    assert columns == report.get_columns()
    assert next(row['value'] for row in summary if row['label'] == 'Saldo neto de conciliación US$') == 25
    assert data[-1]['applied_pending_usd'] == 25
    assert 'signo contable' in message
