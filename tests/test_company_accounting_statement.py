from copy import deepcopy
from unittest.mock import patch

import frappe

from credinomina_reconciliation import company_accounting_statement as statement
from credinomina_reconciliation.accounting_control import build_rows
from credinomina_reconciliation.company_statement import build_detail
from credinomina_reconciliation.conciliacion_credinomina.report.estado_de_cuenta_por_empresa import estado_de_cuenta_por_empresa as report


def evidence(name, debit=0, credit=0, **changes):
    return dict(name=name, employer='ACEREAL', source_account='160209013004', source_currency='USD',
                source_debit=debit, source_credit=credit, source_date='2026-05-15', source_voucher='AS-1',
                source_description=name, source_file_hash='FILE', source_row=1, accounting_source_key=name,
                **changes)


def ledger(items=(), deposits=(), sources=(), imports=None, filters=None):
    rows, _ = build_rows(sources, imports or {}, list(items), deposits=list(deposits), include_financial=False)
    return statement.core_detail(rows, filters)


def credit(amount=63.87, **changes):
    return dict(name='CREDIT', employer='ACEREAL', category='Saldo a favor del cliente', docstatus=1,
                result='Saldo a favor documentado', amount_usd=amount, credit_pending_usd=amount,
                credit_management_status='Pendiente', posting_date='2026-05-15', **changes)


def test_gross_bank_amount_and_credit_reserve_do_not_change_original_ledger():
    deposit = evidence('DEP', credit=3709.78, amount_usd=3770.04, allocated_usd=3706.17,
                       justified_surplus_usd=63.87, docstatus=1)
    original = deepcopy(deposit)
    accounting = ledger([evidence('APP', debit=3706.17)], [deposit])
    management = statement.management_detail(build_detail([], [], [credit()], []))
    total = statement.summarize([*accounting, *management])[0]
    assert total['core_debit_usd'] == 3706.17
    assert total['core_credit_usd'] == 3709.78
    assert total['core_balance_usd'] == -3.61
    assert total['client_credit_usd'] == 63.87
    assert 'balance_usd' not in total
    assert deposit == original


def test_negative_adjustment_transfers_management_without_doubling_receivable():
    accounting = ledger([evidence('APP', debit=100)], [evidence('DEP', credit=90)])
    management = statement.management_detail(build_detail([], [], [], [], adjustment_receivables=[
        dict(name='ADJUSTMENT', employer='ACEREAL', amount_usd=-10, receivable_usd=10,
             receivable_status='Pendiente de cobro', posting_date='2026-05-15')]))
    total = statement.summarize([*accounting, *management])[0]
    assert total['core_balance_usd'] == total['company_receivable_usd'] == 10
    assert total['core_movement_count'] == 2
    assert total['pending_count'] == 1


def test_grouped_origins_and_linked_item_count_each_physical_line_once():
    first = evidence('LINE1', credit=50, accounting_classification='Depósito')
    second = evidence('LINE2', credit=30, accounting_classification='Movimiento interno')
    deposit = dict(first, name='DEP', amount_usd=80, accounting_sources=[
        first, dict(second, origin_complementary_item='ITEM')])
    item = dict(second, name='ITEM', origin_deposit='DEP', category='Vinculado a depósito', docstatus=1)
    source = dict(first, name='SOURCE', parent='IMPORT', event_date='2026-05-15', event_type='Deposito')
    rows = ledger([item], [deposit], [source], {'IMPORT': dict(name='IMPORT', employer='ACEREAL')})
    assert len(rows) == 2
    assert statement.summarize(rows)[0]['core_credit_usd'] == 80
    assert {row['evidence_key'] for row in rows} == {'LINE1', 'LINE2'}
    # An equal-looking physical row at another file position is still a posting.
    rows = ledger([item, dict(second, name='OTHER', accounting_source_key='OTHER', source_row=2)], [deposit],
                  [source], {'IMPORT': dict(name='IMPORT', employer='ACEREAL')})
    assert statement.summarize(rows)[0]['core_credit_usd'] == 110


def test_date_filter_uses_each_original_posting_not_bank_date_or_classification_date():
    first = evidence('EARLY', credit=50)
    second = dict(evidence('LATE', credit=30), source_date='2026-06-01')
    deposit = dict(first, name='DEP', deposit_date='2026-07-01', accounting_sources=[first, second])
    item = evidence('SETTLED', debit=20, docstatus=2, posting_date='2026-07-01',
                    category='Compensación entre partidas', compensated_usd=20)
    rows = ledger([item], [deposit], filters={'cutoff_date': '2026-05-31'})
    assert statement.summarize(rows)[0]['core_balance_usd'] == -30
    assert len(ledger([item], [deposit], filters={'from_date': '2026-06-01'})) == 1


def test_missing_fx_poison_usd_only_and_core_scope_never_loads_management():
    item = dict(evidence('NOFX', debit=366.24), source_currency='NIO', source_fx_rate=0)
    rows = ledger([item])
    total = statement.summarize(rows)[0]
    assert total['core_balance_usd'] is None
    assert total['core_balance_nio'] == 366.24
    with patch.object(report, 'load_core_detail', return_value=rows), \
            patch.object(report, 'load_detail') as management:
        result = report.execute({'statement_scope': statement.CORE})
    management.assert_not_called()
    assert result[1][0]['core_balance_usd'] is None
    assert not any(c['fieldname'] == 'client_credit_usd' for c in result[0])


def test_cutoff_management_and_accounting_are_independent_and_summary_matches_detail():
    core = ledger([evidence('DEBIT', debit=18.46), evidence('CREDIT', credit=32.63)])
    management = build_detail([], [], [credit(18.46)], [])
    with patch.object(report, 'load_core_detail', return_value=core), \
            patch('credinomina_reconciliation.historical_cutoff.cutoff_message', return_value='Corte'), \
            patch('credinomina_reconciliation.historical_cutoff.load_cutoff', return_value=dict(
                detail=management, cutoff_date='2026-08-31', warnings=[])):
        summary = report.execute({'cutoff_date': '2026-08-31'})[1][0]
        detail = report.execute({'cutoff_date': '2026-08-31', 'view_mode': 'Detalle'})[1]
    for field in statement.FIELDS:
        assert round(sum(row.get(field, 0) for row in detail if not row.get('_is_statement_total')), 2) == summary[field]
    assert summary['core_balance_usd'] == -14.17
    assert summary['client_credit_usd'] == 18.46
    assert all(row['cutoff_date'] == '2026-08-31' for row in detail)
    totals = {row['_total_component']: row['statement_amount_usd'] for row in detail if row.get('_is_statement_total')}
    assert totals == {'core_balance_usd': -14.17, 'client_credit_usd': 18.46}


def test_loader_reads_child_origins_and_ignores_manual_documents_without_evidence():
    parent = frappe._dict(name='I', employer='ACEREAL')
    application = frappe._dict(evidence('APP', debit=10, parent='I', event_date='2026-05-15'))
    manual = frappe._dict(name='MANUAL', parent='I', amount=1000, event_type='Aplicacion')
    deposit = frappe._dict(name='DEP', employer='ACEREAL', deposit_date='2026-07-01')
    child = frappe._dict(evidence('ORIGIN', credit=10, parent='DEP'))
    with patch.object(statement.frappe, 'has_permission', return_value=True), \
            patch.object(statement, 'records', side_effect=[[parent], [], [deposit]]), \
            patch.object(statement, 'child_records', return_value=[application, manual]) as children, \
            patch('credinomina_reconciliation.report_records.child_records', return_value=[child]):
        result = statement.load_core_detail({'employer': 'ACEREAL', 'cutoff_date': '2026-05-31'})
    assert children.call_args.args[2:4] == ('CN Accounting Import', 'rows')
    assert statement.summarize(result)[0]['core_balance_usd'] == 0
    assert len(result) == 2


def test_management_drill_does_not_load_core_and_positive_credit_is_not_net():
    rows = build_detail([], [], [credit(60.26)], [])
    with patch.object(report, 'load_core_detail') as core, patch.object(report, 'load_detail', return_value=rows):
        columns, values, _ = report.execute({'component': 'client_credit_usd'})
    core.assert_not_called()
    assert values[0]['client_credit_usd'] == 60.26
    assert not any(c['fieldname'] in {'balance_usd', 'core_balance_usd'} for c in columns)


def test_compact_detail_has_one_amount_and_final_core_balance_without_management():
    core = ledger([evidence('APP', debit=100)], [evidence('DEP', credit=90)])
    management = build_detail([], [], [credit(60.26)], [], adjustment_receivables=[
        dict(name='ADJUSTMENT', employer='ACEREAL', amount_usd=-10, receivable_usd=10,
             receivable_status='Pendiente de cobro', posting_date='2026-05-15')])
    original = deepcopy((core, management))
    with patch.object(report, 'load_core_detail', return_value=core), patch.object(report, 'load_detail', return_value=management):
        columns, rows, _ = report.execute({'view_mode': 'Detalle', 'compact_view': 1})
    assert [col['fieldname'] for col in columns if col['fieldtype'] == 'Currency' and not col.get('hidden')] == ['statement_amount_usd']
    amounts = {row['source_document']: row['statement_amount_usd'] for row in rows if not row.get('_is_statement_total')}
    assert amounts == {'APP': 100, 'DEP': -90, 'CREDIT': 60.26, 'ADJUSTMENT': 10}
    totals = {row['_total_component']: row['statement_amount_usd'] for row in rows if row.get('_is_statement_total')}
    assert totals == {'core_balance_usd': 10, 'client_credit_usd': 60.26, 'company_receivable_usd': 10}
    assert rows[-1]['client_name'] == 'Saldo contable del core'
    assert rows[-1]['statement_amount_usd'] == 10
    assert (core, management) == original


def test_detail_totals_follow_search_scope_and_unknown_conversion():
    core = ledger([evidence('MATCH', debit=10), evidence('OTHER', debit=100),
        dict(evidence('MATCH-NOFX', credit=36.62), source_currency='NIO', source_fx_rate=0)])
    with patch.object(report, 'load_core_detail', return_value=core), patch.object(report, 'load_detail') as management:
        columns, rows, _ = report.execute({'view_mode': 'Detalle', 'statement_scope': statement.CORE,
                                          'compact_view': 1, 'search': 'MATCH', 'component': 'core_balance_nio'})
    management.assert_not_called()
    assert len(rows) == 3
    assert rows[-1]['statement_amount_usd'] is None
    assert rows[-1]['statement_amount_nio'] == -36.62
    assert [col['fieldname'] for col in columns if col['fieldtype'] == 'Currency' and not col.get('hidden')] == ['statement_amount_nio']
    assert report._presentation(core, {'view_mode': 'Detalle', 'search': 'ABSENT'}) == []


def test_management_detail_total_never_fabricates_core_balance():
    management = statement.management_detail(build_detail([], [], [credit(20.77), dict(credit(.01), name='OTHER')], []))
    rows = report._presentation(management, {'view_mode': 'Detalle'})
    assert len(rows) == 3
    assert rows[-1]['_total_component'] == 'client_credit_usd'
    assert rows[-1]['statement_amount_usd'] == 20.78
    assert 'core_balance_usd' not in rows[-1]
