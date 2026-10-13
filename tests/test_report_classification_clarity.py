"""Financial states, filtered totals and classification must agree across views."""
from copy import deepcopy

import pytest

from credinomina_reconciliation.accounting_control import _status
from credinomina_reconciliation.client_position import build_position, summary
from credinomina_reconciliation.company_statement import build_detail
from credinomina_reconciliation.complementary_balances import financial_balance, reconciliation_state
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_operativo.estado_de_cuenta_operativo import get_columns


def credit(**changes):
    return dict(dict(name='C', employer='E', docstatus=1, category='Saldo a favor del cliente',
        amount_usd=20, posting_date='2025-05-10', result='Saldo a favor documentado',
        credit_management_status='Parcialmente resuelto', credit_pending_usd=15,
        credit_resolved_usd=5, reason_type='Por cancelación', accounting_status='Pendiente de registro'), **changes)


def position(item, filters=None, distributions=()):
    return build_position([], [], [item], {item['name']: financial_balance(item, distributions)}, [], filters or {})


def test_documented_credit_is_conciliated_in_report_and_keeps_external_work():
    item = credit()
    original = deepcopy(item)
    rows = position(item, dict(position_type='Partida complementaria', operational_status='Conciliado',
        reason_type='Por cancelación', accounting_status='Pendiente de registro',
        management_status='Parcialmente resuelto', treatment_status='Documentado', only_open=1))
    assert len(rows) == 1
    assert rows[0]['complementary_pending_usd'] == 0
    assert rows[0]['credit_pending_usd'] == 15
    assert rows[0]['treatment_status'] == 'Documentado'
    assert reconciliation_state(financial_balance(item)) == _status({}, item) == rows[0]['operational_status'] == 'Conciliado'
    statement = build_detail([], [], [item], [])[0]
    assert statement['status'] == 'Conciliado'
    assert statement['management_status'] == 'Parcialmente resuelto'
    assert statement['client_credit_usd'] == -15
    assert item == original


@pytest.mark.parametrize('used,state', [(0, 'Pendiente'), (40, 'Parcial'), (100, 'Conciliado'), (110, 'Revisar distribución')])
def test_cash_complement_states_agree_without_changing_classification(used, state):
    item = credit(category='Otros ingresos', amount_usd=100, subcategory='Otro ajuste', reason_type='Otro por aclarar')
    balance = financial_balance(item, [dict(amount_usd=used)])
    item['_balance'] = balance
    item['_signed_pending_usd'] = balance['pending_usd']
    rows = position(item, dict(operational_status=state), [dict(amount_usd=used)])
    assert len(rows) == 1
    assert reconciliation_state(balance) == _status({}, dict(item, _financial_balance=balance)) == state
    if used != 100:
        assert build_detail([], [item], [], [])[0]['status'] == state
    assert rows[0]['category'] == 'Otros ingresos'
    assert rows[0]['subcategory'] == 'Otro ajuste'


def test_classification_filters_do_not_discard_an_unsettled_non_conciliatory_amount():
    item = credit(category='Por clasificar', docstatus=0, amount_usd=100,
                  review_action='No conciliatoria', review_status='No conciliatoria')
    rows = position(item, dict(operational_status='Pendiente', treatment_status='Por aclarar (no conciliatoria)', category='Por clasificar'))
    assert len(rows) == 1
    assert rows[0]['complementary_pending_usd'] == 100
    assert position(item, dict(category='Otros ingresos')) == []


def test_each_selected_type_has_its_own_totals_even_with_no_matching_rows():
    labels = {
        'Aplicación': ['Aplicado neto US$', 'Depósito asignado US$', 'CxC por aplicaciones US$'],
        'Partida complementaria': ['Complementarias pendientes de conciliar US$', 'Saldos a favor por gestionar US$', 'Partidas pendientes de registro contable'],
        'Cobranza': ['Cobranza US$', 'Aplicado para control US$', 'Filas con diferencias de control'],
        'CxC por ajuste': ['CxC por ajustes US$'],
    }
    for kind, expected in labels.items():
        totals = summary([], {'position_type': kind})
        assert [figure['label'] for figure in totals] == expected
        assert all(figure['value'] == 0 for figure in totals)
    assert summary([]) == []


def test_complement_totals_do_not_net_separate_obligations_or_include_excluded_apps():
    items = [credit(), credit(name='C2', category='Otros ingresos', amount_usd=-50, docstatus=0)]
    rows = build_position([], [], items, {item['name']: financial_balance(item) for item in items}, [], {})
    totals = {figure['label']: figure['value'] for figure in summary(rows)}
    assert totals['Complementarias pendientes de conciliar US$'] == 50
    assert totals['Saldos a favor por gestionar US$'] == 15
    assert totals['Partidas pendientes de registro contable'] == 2
    assert 'CxC por aplicaciones US$' not in totals


def test_unknown_application_amount_is_not_a_zero_or_partial_total():
    rows = [dict(position_type='Aplicación', applied_usd=100, remitted_usd=60, applied_pending_usd=40),
            dict(position_type='Aplicación', applied_usd=None, remitted_usd=0, applied_pending_usd=None)]
    totals = {figure['label']: figure for figure in summary(rows)}
    assert 'CxC por aplicaciones US$' not in totals
    assert totals['CxC por aplicaciones US$ · importes sin determinar']['value'] == 1
    assert totals['CxC por aplicaciones US$ · importes sin determinar']['datatype'] == 'Int'
    assert totals['Depósito asignado US$']['value'] == 60


def test_credit_with_conflicting_original_evidence_stays_under_review_in_both_reports():
    item = credit(accounting_source_key='core', source_currency='USD', source_debit=30, source_credit=0)
    assert position(item)[0]['operational_status'] == 'Revisar distribución'
    assert build_detail([], [], [item], [])[0]['status'] == 'Revisar distribución'
    assert build_detail([], [], [item], [])[0]['client_credit_usd'] == -15


def test_classification_is_visible_in_compact_complement_report_and_traceable_in_detail():
    compact = {column['fieldname']: column for column in get_columns({'position_type': 'Partida complementaria'})}
    assert not compact['category'].get('hidden')
    assert not compact['accounting_status'].get('hidden')
    assert not compact['management_status'].get('hidden')
    assert list(compact)[:3] == ['event_date', 'employer', 'category']
    assert compact['treatment_status']['hidden'] == 1
    expanded = {column['fieldname']: column for column in get_columns({'position_type': 'Partida complementaria', 'detailed_columns': 1})}
    assert not expanded['treatment_status'].get('hidden')
    assert not expanded['reason_type'].get('hidden')
