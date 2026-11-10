from copy import deepcopy
import json
from unittest.mock import patch

import frappe
import pytest

from credinomina_reconciliation import reconciliation_pending_detail as pending
from credinomina_reconciliation import unallocated_deposit_position as positions
from credinomina_reconciliation.allocation_origin import MANUAL
from credinomina_reconciliation.report_employers import selected_employers
from credinomina_reconciliation.conciliacion_credinomina.report.resumen_de_conciliacion import resumen_de_conciliacion as summary
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as statement


PERIOD = dict(name='P', employer='INDENICSA', payroll_month='2026-05-01', collection_cycle='Mensual')


def deposit(**changes):
    return dict(dict(name='DEP', docstatus=1, employer='INDENICSA', deposit_date='2026-05-15',
        amount_usd=40, allocated_usd=20, justified_surplus_usd=0, unclassified_usd=20,
        allocation_detail=json.dumps([dict(tipo='Aplicacion historica', fila_detalle='ROW', importe_usd=20, periodo='P')])), **changes)


def detail(**changes):
    return dict(dict(name='ROW', parent='DEP', client_number='123', client_name='Juan', loan_number='0012-1', amount_usd=40), **changes)


def build(deposits=(), details=(), links=(), items=(), balances=None, filters=None, periods=None):
    return pending.build([PERIOD] if periods is None else periods, deposits, details, links, items, balances or {}, filters or {})


def test_partial_detail_is_a_separate_line_for_only_actual_unassigned_cash():
    sources = [deposit()], [detail()]
    original = deepcopy(sources)
    rows = build(*sources)
    assert len(rows) == 1
    row = rows[0]
    assert (row['name'], row['client_number'], row['client_name'], row['deposit_usd']) == ('P', '123', 'Juan', 20)
    assert (row['application_usd'], row['open_usd'], row['cash_adjustment_usd']) == (0, 0, 0)
    assert row['has_difference'] and row['cash_control'].startswith('Revisar:')
    assert 'DEP' in row['observations'] and '15/05/2026' in row['observations']
    assert sources == original


def test_paid_manual_details_from_a_multi_period_deposit_do_not_reappear_as_pending():
    ledger, details, targets = [], [], []
    for name, amount in (('FIRST', 15.45), ('SECOND', 292)):
        ledger.append(dict(tipo='Aplicacion historica', periodo='P', aplicacion_id=name, origen=MANUAL, importe_usd=amount))
        targets.append(dict(name='T-' + name, parent='DEP', historical_application=name,
                            detail_row=name, amount_usd=amount, result='Aplicada'))
        details.append(detail(name=name, amount_usd=amount, linked_usd=amount, pending_usd=0,
            matched_targets=json.dumps([dict(claim_id='H:' + name, instruction_id='T-' + name, amount_usd=amount)])))
    ledger.append(dict(tipo='Aplicacion historica', periodo='OTHER', aplicacion_id='THIRD',
                       fila_detalle='THIRD', importe_usd=15.45))
    details.extend([detail(name='THIRD', amount_usd=15.45), detail(name='OTHER-CLIENT', client_number='456', amount_usd=.06)])
    dep = deposit(allocation_detail=json.dumps(ledger), amount_usd=322.93, allocated_usd=322.90, unclassified_usd=.03)
    original = deepcopy((dep, details, targets))

    def children(doctype, parents, parenttype, parentfield, *, fields, **kwargs):
        assert list(parents) == ['DEP'] and parenttype == 'CN Remittance Allocation'
        source = {'CN Remittance Detail': ('detail_rows', details),
                  'CN Remittance Period': ('detail_periods', [dict(parent='DEP', period='OTHER')]),
                  'CN Remittance Target': ('targets', targets)}
        field, rows = source[doctype]
        assert field == parentfield
        return [{key: row.get(key) for key in fields} for row in rows]

    with patch.object(frappe, 'has_permission', return_value=True), \
            patch.object(pending, 'records', side_effect=lambda dt, **kw: [dep] if dt == 'CN Remittance Allocation' else []), \
            patch.object(pending, 'child_records', side_effect=children), \
            patch.object(positions, 'child_records', side_effect=children):
        rows = pending.load([PERIOD, dict(PERIOD, name='OTHER')], {})
    assert [(row['client_number'], row['deposit_usd']) for row in rows] == [('456', None), ('', .03)]
    assert (dep, details, targets) == original


@pytest.mark.parametrize('conflict', ['pending', 'shared', 'target_amount', 'instruction', 'claim', 'explicit_other_row', 'automatic'])
def test_inconclusive_legacy_manual_links_do_not_hide_cash(conflict):
    payment = dict(tipo='Aplicacion historica', aplicacion_id='A', periodo='P', origen=MANUAL, importe_usd=20)
    target = dict(name='T', parent='DEP', detail_row='ROW', historical_application='A', amount_usd=20, result='Aplicada')
    match = dict(instruction_id='T', claim_id='H:A', amount_usd=20)
    targets = [target]
    if conflict == 'pending':
        target['result'] = 'Pendiente'
    elif conflict == 'shared':
        targets.append(dict(target, name='T2', detail_row='ANOTHER'))
    elif conflict == 'target_amount':
        target['amount_usd'] = 21
    elif conflict == 'instruction':
        match['instruction_id'] = 'OTHER'
    elif conflict == 'claim':
        match['claim_id'] = 'H:OTHER'
    elif conflict == 'explicit_other_row':
        payment['fila_detalle'] = 'ANOTHER'
    elif conflict == 'automatic':
        payment['origen'] = 'Automática FIFO'
    rows = positions.build([deposit(allocation_detail=[payment])], [detail(matched_targets=[match])], targets)
    assert [(r['client_name'], r['deposit_pending_usd']) for r in rows] == [('Juan', None), ('Sin cliente individual', 20)]


def test_manual_cash_split_between_old_and_explicit_entries_is_counted_once():
    payment = dict(tipo='Cobranza', periodo='P', fila_id='INSTALMENT', origen=MANUAL, importe_usd=8)
    ledger = [payment, dict(payment, fila_detalle='ROW', importe_usd=12)]
    target = dict(name='T', parent='DEP', detail_row='ROW', period='P', row_key='INSTALMENT', amount_usd=20, result='Aplicada')
    row = detail(matched_targets=[dict(instruction_id='T', claim_id='C:CHILD', amount_usd=20)])
    result = positions.build([deposit(allocation_detail=ledger)], [row], [target])
    assert [(r['client_name'], r['deposit_pending_usd']) for r in result] == [('Juan', 20)]


@pytest.mark.parametrize('first, second', [(15, 25), (20, 20)])
def test_shared_application_uses_each_confirmed_instruction_once(first, second):
    details, targets, ledger = [], [], []
    for row, amount in [('A', first), ('B', second)]:
        targets.append(dict(name='T' + row, parent='DEP', detail_row=row, historical_application='SHARED',
                            amount_usd=amount, result='Aplicada'))
        details.append(detail(name=row, client_number=row, amount_usd=amount + (5 if row == 'B' else 0),
            matched_targets=[dict(instruction_id='T' + row, claim_id='H:SHARED', amount_usd=amount)]))
        ledger.append(dict(tipo='Aplicacion historica', aplicacion_id='SHARED', origen=MANUAL, importe_usd=amount))
    dep = deposit(allocation_detail=ledger, amount_usd=45, allocated_usd=40, unclassified_usd=5)
    for explicit in (False, True):
        if explicit:
            ledger[0]['fila_detalle'] = 'A'
        rows = positions.build([dep], details, targets)
        assert [(r['client_number'], r['deposit_pending_usd']) for r in rows] == [('B', 5)]
    # Equal journal and target totals alone cannot establish individual amounts.
    ledger[0].pop('fila_detalle')
    ledger[0]['importe_usd'], ledger[1]['importe_usd'] = 10, 30
    rows = positions.build([dep], details, targets)
    assert [r['deposit_pending_usd'] for r in rows] == [None, None, 5]


def test_credits_do_not_reappear_and_fully_distributed_cash_is_excluded():
    assert build([deposit(allocated_usd=40, unclassified_usd=0)], [detail()]) == []
    rows = build([deposit(justified_surplus_usd=10, unclassified_usd=10)], [detail(client_credit_usd=10)])
    assert len(rows) == 1 and rows[0]['deposit_usd'] == 10


def test_unlinked_deposit_without_client_is_once_without_invented_period():
    rows = build([deposit(allocated_usd=0, unclassified_usd=40, allocation_detail='[]')], filters={'period': 'P'})
    assert len(rows) == 1 and rows[0]['name'] is None
    assert rows[0]['client_name'] == 'Sin cliente individual' and rows[0]['deposit_usd'] == 40
    assert 'Sin atribución única' in rows[0]['observations']
    assert build([deposit(deposit_date='2026-06-01', allocated_usd=0, unclassified_usd=40, allocation_detail='[]')], filters={'period':'P'}) == []


def test_multi_period_cash_is_not_repeated_or_prorated():
    rows = build([deposit()], [detail()], [dict(parent='DEP', period='P2')],
                 periods=[PERIOD, dict(PERIOD, name='P2')])
    assert len(rows) == 1 and rows[0]['name'] is None and rows[0]['deposit_usd'] == 20
    assert build([deposit()], [detail()], filters={'period':'OTHER'}, periods=[dict(PERIOD, name='OTHER')]) == []


def test_linked_origins_and_manual_drafts_never_duplicate_cash():
    for changes in (dict(consolidated_deposit='CANONICAL'), dict(cash_identity_status='Origen vinculado'), dict(docstatus=0), dict(docstatus=2)):
        assert build([deposit(**changes)], [detail()]) == []
    rows = build([deposit(docstatus=0, accounting_source_key='CORE')], [detail()])
    assert len(rows) == 1 and rows[0]['deposit_usd'] == 40
    assert 'sin confirmar' in rows[0]['observations']


def test_planned_targets_and_corrupt_evidence_cannot_fake_individual_cash():
    rows = build([deposit(allocated_usd=0, unclassified_usd=40, allocation_detail='[]')], [detail(pending_usd=0)])
    assert rows[0]['deposit_usd'] == 40  # Cached/planned pending is not actual execution.
    rows = build([deposit(allocation_detail='broken')], [detail()])
    individual = next(r for r in rows if r['client_number'])
    assert individual['deposit_usd'] is None and individual['open_usd'] == 0
    assert sum(r['deposit_usd'] or 0 for r in rows) == 20


def test_unlinked_complement_remainder_preserves_client_and_accounting_direction():
    for debit, credit, expected in ((13, 0, 3), (0, 13, -3)):
        item = dict(name='COMP', employer='INDENICSA', posting_date='2026-05-20', docstatus=1,
            client_number='123', client_name='Juan', loan_number='0012-1', category='Ajuste de conciliación',
            amount_usd=13, accounting_source_key='CORE', source_debit=debit, source_credit=credit)
        balance = dict(pending_usd=3, used_usd=10, financial_status='Parcial')
        original = deepcopy((item, balance))
        rows = build(items=[item], balances={'COMP':balance}, filters={'period':'P'})
        assert len(rows) == 1 and rows[0]['name'] is None
        assert rows[0]['client_number'] == '123' and rows[0]['cash_adjustment_usd'] == expected
        assert rows[0]['deposit_usd'] == rows[0]['open_usd'] == 0
        assert 'COMP' in rows[0]['observations']
        assert (item, balance) == original
        for changes, remaining in (({'origin_deposit':'DEP'}, 3), ({'docstatus':2}, 3), ({}, 0)):
            assert build(items=[dict(item, **changes)], balances={'COMP':dict(balance, pending_usd=remaining)}) == []


def test_complement_missing_or_invalid_direction_remains_undetermined():
    item = dict(name='COMP', employer='INDENICSA', period='P', posting_date='2026-05-20', docstatus=1,
                amount_usd=5, accounting_source_key='CORE')
    rows = build(items=[item], balances={'COMP':dict(pending_usd=5)})
    assert rows[0]['cash_adjustment_usd'] is None and rows[0]['name'] == 'P'


def test_optional_company_scope_is_direct_not_transitive_or_reverse():
    mapping = {'INDENICSA': {'CBC'}, 'CBC': {'THIRD'}, 'OTHER': {'INDENICSA'}}
    with patch('credinomina_reconciliation.paying_employers.load_payer_map', return_value=mapping) as query:
        assert selected_employers({'employer':'INDENICSA'}) == ['INDENICSA']
        assert selected_employers({'include_paying_companies':1}) == []
        query.assert_not_called()
        assert selected_employers({'employer':'INDENICSA', 'include_paying_companies':'1'}) == ['CBC', 'INDENICSA']


def test_payer_deposit_is_filtered_by_beneficiary_after_calculating_actual_cash():
    dep = deposit(allocated_usd=0, unclassified_usd=40, allocation_detail='[]')
    details = [detail(employer='CBC', amount_usd=25), detail(name='ROW2', employer='INDENICSA', amount_usd=15)]
    period_links = [dict(parent='DEP', period='P')]
    own = build([dep], details, period_links, filters={'employer':'INDENICSA'})
    assert [(row['name'], row['deposit_usd']) for row in own] == [('P', 15)]
    other = build([dep], details, period_links, filters={'employer':'CBC'}, periods=[])
    assert [(row['employer'], row['name'], row['deposit_usd']) for row in other] == [('CBC', None, 25)]
    assert build([dep], details, period_links, filters={'period':'P'}) == own
    with patch('credinomina_reconciliation.paying_employers.load_payer_map', return_value={'INDENICSA': {'CBC'}}):
        both = build([dep], details, period_links, filters={'employer':'INDENICSA', 'include_paying_companies':1})
    assert [(row['employer'], row['name'], row['deposit_usd']) for row in both] == [('CBC', None, 25), ('INDENICSA', 'P', 15)]


def test_summary_expands_period_query_and_only_detail_loads_pending_lines():
    with patch('credinomina_reconciliation.paying_employers.load_payer_map', return_value={'INDENICSA': {'CBC'}}), \
            patch.object(summary, 'records', return_value=[]) as query, \
            patch.object(summary, 'child_records', return_value=[]), \
            patch.object(summary, 'load_direct_applications', return_value=[]), \
            patch.object(summary, 'load_pending_detail', return_value=[]) as load:
        summary.execute(dict(employer='INDENICSA', include_paying_companies=1))
        assert query.call_args.kwargs['filters']['employer'] == ['in', ['CBC', 'INDENICSA']]
        load.assert_not_called()
        summary.execute(dict(view='Detalle'))
        load.assert_called_once()


def test_company_statement_expands_all_readers_before_one_summary():
    with patch('credinomina_reconciliation.paying_employers.load_payer_map', return_value={'INDENICSA': {'CBC'}}), \
            patch.object(statement, 'load_core_detail', return_value=[]) as core, \
            patch.object(statement, 'load_detail', return_value=[]) as management, \
            patch.object(statement, 'load_deposit_differences', return_value=[]) as differences:
        statement.execute(dict(employer='INDENICSA', include_paying_companies=1))
        for query in (core, management, differences):
            assert [c.args[0]['employer'] for c in query.call_args_list] == ['CBC', 'INDENICSA']


def test_pending_detail_keeps_columns_and_suppresses_mixed_coverage_totals():
    extra = build([deposit(allocated_usd=0, unclassified_usd=40, allocation_detail='[]')])
    with patch.object(summary, 'records', return_value=[]), \
            patch.object(summary, 'child_records', return_value=[]), \
            patch.object(summary, 'load_direct_applications', return_value=[]), \
            patch.object(summary, 'load_pending_detail', return_value=extra):
        columns, rows, message, _, _, skip = summary.execute({'view':'Detalle', 'only_differences':1})
        assert rows == extra and skip and 'remanentes independientes' in message
        assert len(columns) == 24
        assert not {'detail_received_usd', 'detail_unassigned_usd', 'source_document'} & {c['fieldname'] for c in columns}


def test_company_cutoff_merges_warnings_and_keeps_one_presentation():
    with patch('frappe.utils.formatdate', side_effect=str), \
            patch('credinomina_reconciliation.paying_employers.load_payer_map', return_value={'INDENICSA': {'CBC'}}), \
            patch('credinomina_reconciliation.historical_cutoff.load_cutoff', side_effect=lambda scope: dict(
                cutoff_date=scope['cutoff_date'], detail=[], warnings=[scope['employer']])) as cutoff, \
            patch.object(statement, 'load_deposit_differences', return_value=[]):
        result = statement.execute(dict(employer='INDENICSA', include_paying_companies=1,
            cutoff_date='2026-05-31', statement_scope='Gestión'))
        assert [c.args[0]['employer'] for c in cutoff.call_args_list] == ['CBC', 'INDENICSA']
        assert 'CBC' in result[2] and 'INDENICSA' in result[2]


def test_bridge_expands_per_company_without_combining_their_balances():
    from credinomina_reconciliation import company_statement_bridge as bridge
    with patch('frappe.utils.formatdate', side_effect=str), \
            patch('credinomina_reconciliation.paying_employers.load_payer_map', return_value={'INDENICSA': {'CBC'}}), \
            patch.object(bridge, 'load_core_detail', return_value=[]), \
            patch.object(bridge, 'load_cutoff', side_effect=lambda scope, **kw: dict(
                cutoff_date=scope['cutoff_date'], warnings=[])), \
            patch.object(bridge, 'build_bridge', side_effect=lambda core, snapshot, employer: [dict(employer=employer)]), \
            patch.object(bridge, 'load_deposit_differences', return_value=[]), \
            patch('credinomina_reconciliation.difference_reasons.attach_treatments'):
        rows = bridge.execute(dict(employer='INDENICSA', include_paying_companies=1, cutoff_date='2026-05-31'))[1]
        assert [row['employer'] for row in rows] == ['CBC', 'INDENICSA']
